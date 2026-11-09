import ctypes as C
from ctypes import wintypes as W
import base64
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

from pc_fish_data import GameData

RARITIES = ("기본", "일반", "고급", "희귀", "전설", "신화")
DATA = Path(os.environ.get("LOCALAPPDATA", ".")) / "PCFishHelper" / "settings.json"
U = C.WinDLL("user32")
G = C.WinDLL("gdi32")
K = C.WinDLL("kernel32")


def api(lib, name, result, *args):
    func = getattr(lib, name)
    func.restype, func.argtypes = result, args
    return func


get_dc = api(U, "GetDC", W.HDC, W.HWND)
release_dc = api(U, "ReleaseDC", C.c_int, W.HWND, W.HDC)
compatible_dc = api(G, "CreateCompatibleDC", W.HDC, W.HDC)
delete_dc = api(G, "DeleteDC", W.BOOL, W.HDC)
bitmap = api(G, "CreateCompatibleBitmap", W.HBITMAP, W.HDC, C.c_int, C.c_int)
select = api(G, "SelectObject", W.HANDLE, W.HDC, W.HANDLE)
delete = api(G, "DeleteObject", W.BOOL, W.HANDLE)
blit = api(G, "BitBlt", W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int,
           W.HDC, C.c_int, C.c_int, W.DWORD)
pixels = api(G, "GetDIBits", C.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT,
             C.c_void_p, C.c_void_p, W.UINT)
window_at = api(U, "WindowFromPoint", W.HWND, W.POINT)
ancestor = api(U, "GetAncestor", W.HWND, W.HWND, W.UINT)
window_pid = api(U, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
open_process = api(K, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
close = api(K, "CloseHandle", W.BOOL, W.HANDLE)
process_name = api(K, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD,
                   W.LPWSTR, C.POINTER(W.DWORD))


def game_at(x, y):
    window = window_at(W.POINT(x, y))
    if not window:
        return False
    pid = W.DWORD()
    window_pid(ancestor(window, 2), C.byref(pid))
    process = open_process(0x1000, False, pid.value)
    if not process:
        return False
    try:
        name = C.create_unicode_buffer(32768)
        size = W.DWORD(len(name))
        return bool(process_name(process, 0, name, C.byref(size))
                    and Path(name.value).name.casefold() == "pcfish.exe")
    finally:
        close(process)


def capture(x, y, width=24, height=24):
    screen = get_dc(None)
    memory = compatible_dc(screen)
    frame = bitmap(screen, width, height)
    previous = select(memory, frame)
    try:
        ok = blit(memory, 0, 0, width, height, screen, x - width // 2,
                  y - height // 2, 0x40CC0020)
        select(memory, previous)
        header = (W.DWORD * 10)(40, width, height, (32 << 16) | 1)
        data = C.create_string_buffer(width * height * 4)
        if not ok or pixels(screen, frame, 0, height, data, header, 0) != height:
            raise OSError("화면을 읽을 수 없습니다.")
        return data.raw
    finally:
        delete(frame)
        delete_dc(memory)
        release_dc(None, screen)


def matches(template):
    x, y, encoded = template
    return game_at(x, y) and capture(x, y) == base64.b64decode(encoded)


def click(template):
    if not matches(template):
        return False
    x, y, _ = template
    if not U.SetCursorPos(x, y) or not game_at(x, y):
        return False
    U.mouse_event(2, 0, 0, 0, 0)
    U.mouse_event(4, 0, 0, 0, 0)
    return True


def match_badge(frame, samples):
    # DIB rows are bottom-up. A small search tolerates registration offsets.
    width, height = 42, 24
    stride = 52 * 4
    for top in range(11):
        for left in range(11):
            start = top * stride + left * 4
            badge = b"".join(frame[offset:offset + width * 4]
                             for offset in range(start, start + stride * height, stride))
            rating = samples.get(badge)
            if rating is not None:
                return rating
    return None


def merge_distribution(levels):
    if len(levels) != 10 or any(level not in range(1, 6) for level in levels):
        raise ValueError("기본 물고기를 제외한 재료 10마리가 필요합니다.")
    level, remainder = divmod(sum(levels) + 10, 10)
    if level >= 5:
        return [(5, 100)]
    result = [(level, 100 - remainder * 10)]
    if remainder:
        result.append((level + 1, remainder * 10))
    return result


class Helper:
    def __init__(self, root):
        self.root = root
        self.data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
        self.fish = self.data.setdefault("fish", [])
        self.game = None
        self.synced_state = None
        self.mergeable = None
        self.next_sync = 0
        self.waiting = None
        self.dynamic = None
        self.routes = self.data.setdefault("routes", [])
        self.running = [tk.BooleanVar(), tk.BooleanVar()]
        self.steps = []
        self.pending = []
        self.last_action = 0
        self.deadline = 0
        self.status = tk.StringVar(value="자동화 꺼짐")
        root.title("PC FISH 보조 도구")
        root.geometry("800x570")
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        auto, inventory, merge = (ttk.Frame(tabs, padding=12) for _ in range(3))
        for tab, name in zip((auto, inventory, merge), ("자동화", "도감·보유", "합성 계산기")):
            tabs.add(tab, text=name)
        for flag, name in zip(self.running, ("자동 교배", "자동 수조 레벨업")):
            ttk.Checkbutton(auto, text=name, variable=flag, command=self.toggle).pack(anchor="w")
        ttk.Label(auto, textvariable=self.status).pack(anchor="w", pady=8)
        ttk.Label(auto, text="교배 경로를 위에서부터 우선 적용합니다. F8을 누르면 자동화가 멈춥니다.").pack(anchor="w")
        self.route_list = tk.Listbox(auto, height=7, exportselection=False)
        self.route_list.pack(fill="x", pady=6)
        bar = ttk.Frame(auto)
        bar.pack(fill="x")
        for label, handler in (
            ("위로", lambda: self.move(-1)), ("아래로", lambda: self.move(1)),
            ("경로 삭제", self.remove_route), ("교배 단계 등록", self.record_step),
            ("교배 경로 저장", self.store_route), ("수조 버튼 등록", self.record_upgrade),
        ):
            ttk.Button(bar, text=label, command=handler).pack(side="left", padx=2)
        ttk.Label(auto, wraplength=690, text=(
            "등록을 누른 뒤 4초 안에 게임의 클릭할 버튼 위에 마우스를 놓으세요. "
            "교배는 부모 슬롯, 후보, 두 번째 슬롯, 후보, 교배 버튼 순으로 등록하세요. "
            "각 단계 등록이 끝난 뒤 직접 눌러 다음 화면을 여세요. 마지막 교배 버튼은 직접 누를 필요가 없습니다. "
            "수조 버튼은 경험치가 가득 차 실제 레벨업이 가능할 때 등록하세요."
        )).pack(anchor="w", pady=12)
        self.route_name = ttk.Entry(auto)
        self.route_name.insert(0, "교배 후보 조합")
        self.route_name.pack(fill="x")
        self.refresh_routes()
        extra = ttk.Frame(auto)
        extra.pack(fill="x", pady=8)
        ttk.Button(extra, text="교배 결과 닫기 등록",
                   command=lambda: self.record_action("dismiss")).pack(side="left")
        ttk.Button(extra, text="수조 탭 등록",
                   command=lambda: self.record_action("tank_tab")).pack(side="left")
        ttk.Button(extra, text="교배 탭 등록",
                   command=lambda: self.record_action("breed_tab")).pack(side="left")
        ttk.Button(extra, text="등록 중인 단계 초기화", command=self.steps.clear).pack(side="left")
        scan = ttk.LabelFrame(auto, text="후보 자동 선택 · 한국어 UI 1배율", padding=6)
        scan.pack(fill="x", pady=4)
        self.scan_order = ttk.Combobox(scan, state="readonly", width=18,
                                       values=("희귀도 높은 순", "희귀도 낮은 순", "별 등급 높은 순", "별 등급 낮은 순"))
        self.scan_order.current(self.data.get("scan_order", 0))
        self.scan_order.bind("<<ComboboxSelected>>", self.save_scan_order)
        self.scan_order.pack(side="left", padx=3)
        self.calibration = ttk.Combobox(scan, state="readonly", width=12,
            values=("첫 부모 슬롯", "둘째 부모 슬롯", "후보 첫 카드", "활성 교배 버튼"))
        self.calibration.current(0)
        self.calibration.pack(side="left", padx=3)
        ttk.Button(scan, text="위치 등록", command=self.record_scan).pack(side="left")
        self.badge = ttk.Combobox(scan, state="readonly", width=12,
            values=["기본"] + [f"{rarity} {star}성" for rarity in RARITIES[1:] for star in range(1, 6)])
        self.badge.current(0)
        self.badge.pack(side="left", padx=(10, 3))
        ttk.Button(scan, text="후보 등급 등록", command=self.record_badge).pack(side="left")

        form = ttk.Frame(inventory)
        form.pack(fill="x")
        self.name = ttk.Entry(form, width=22)
        self.name.pack(side="left")
        self.rarity = ttk.Combobox(form, values=RARITIES, state="readonly", width=7)
        self.rarity.current(1)
        self.rarity.pack(side="left")
        self.stars = ttk.Spinbox(form, from_=1, to=5, width=5)
        self.stars.set(1)
        self.stars.pack(side="left")
        self.amount = ttk.Spinbox(form, from_=0, to=999, width=6)
        self.amount.set(1)
        self.amount.pack(side="left")
        ttk.Button(form, text="보유 수량 저장", command=self.save_fish).pack(side="left")
        self.sync_status = tk.StringVar(value="로컬 도감 읽는 중")
        ttk.Label(inventory, textvariable=self.sync_status).pack(anchor="w", pady=4)
        ttk.Label(inventory, text="어종 이름 · 희귀도 · 별 등급 · 현재 보유 수량").pack(anchor="w")
        self.owned_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(inventory, text="보유한 어종만", variable=self.owned_only,
                        command=self.refresh_inventory).pack(anchor="w")
        self.inventory = tk.Listbox(inventory)
        self.inventory.pack(fill="both", expand=True, pady=8)
        self.inventory.bind("<<ListboxSelect>>", self.load_fish)

        self.filters = []
        for values in (RARITIES, tuple(str(i) for i in range(1, 6))):
            row = ttk.Frame(merge)
            row.pack(fill="x")
            flags = [tk.BooleanVar(value=True) for _ in values]
            self.filters.append(flags)
            for value, flag in zip(values, flags):
                ttk.Checkbutton(row, text=value, variable=flag, command=self.refresh_merge).pack(side="left")
        ttk.Label(merge, text="도감 보유량에서 재료를 선택하세요. 수량을 넣고 여러 어종을 추가할 수 있습니다.").pack(anchor="w", pady=5)
        self.candidates = tk.Listbox(merge, height=8)
        self.candidates.pack(fill="both", expand=True)
        row = ttk.Frame(merge)
        row.pack(fill="x")
        self.quantity = ttk.Spinbox(row, from_=1, to=10, width=5)
        self.quantity.set(1)
        self.quantity.pack(side="left")
        ttk.Button(row, text="재료 추가", command=self.add_material).pack(side="left")
        ttk.Button(row, text="재료 초기화", command=self.clear_materials).pack(side="left")
        self.result = tk.StringVar()
        ttk.Label(merge, textvariable=self.result, wraplength=690).pack(anchor="w")
        self.materials = {}
        self.season = ttk.Combobox(merge, state="readonly")
        self.season.pack(fill="x", pady=(8, 2))
        self.season.bind("<<ComboboxSelected>>", lambda _event: self.show_recipe())
        self.recipe_summary = tk.StringVar()
        ttk.Label(merge, textvariable=self.recipe_summary, wraplength=730).pack(anchor="w")
        self.refresh_inventory()
        self.root.after(200, self.tick)

    def sync(self):
        now = time.monotonic()
        if now < self.next_sync:
            return
        self.next_sync = now + 2
        try:
            if self.game is None:
                self.game = GameData()
            state = self.game.state()
            fish, mergeable = self.game.rows()
            if fish != self.fish or mergeable != self.mergeable:
                self.fish, self.mergeable = fish, mergeable
                self.materials.clear()
                self.refresh_inventory()
            self.synced_state = state
            if not self.season.cget("values") and self.game.recipes:
                self.season["values"] = ["시즌 레시피 선택"] + [
                    f"시즌 {season} · {name} · {stars}성"
                    for season, name, stars in sorted(self.game.recipes)]
                self.season.current(0)
            self.show_recipe()
            owned = sum(row[3] for row in self.fish)
            self.sync_status.set(f"게임 동기화 · 보유 {owned}마리 · 수조 Lv.{state['tankLevel']}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.synced_state = None
            self.sync_status.set(f"게임 동기화 대기 · {error}")

    def save(self):
        DATA.parent.mkdir(parents=True, exist_ok=True)
        temporary = DATA.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(DATA)

    def toggle(self):
        self.pending.clear()
        self.dynamic = None
        self.waiting = None
        self.status.set("등록된 게임 화면 대기 중" if any(f.get() for f in self.running) else "자동화 꺼짐")

    def refresh_routes(self):
        self.route_list.delete(0, tk.END)
        for route in self.routes:
            self.route_list.insert(tk.END, route["name"])

    def move(self, direction):
        choice = self.route_list.curselection()
        if not choice:
            return
        i = choice[0]
        j = i + direction
        if 0 <= j < len(self.routes):
            self.routes[i], self.routes[j] = self.routes[j], self.routes[i]
            self.refresh_routes()
            self.route_list.selection_set(j)
            self.save()

    def remove_route(self):
        choice = self.route_list.curselection()
        if choice:
            self.stop("교배 경로 삭제")
            self.routes.pop(choice[0])
            self.refresh_routes()
            self.save()

    def stop(self, reason):
        for flag in self.running:
            flag.set(False)
        self.pending.clear()
        self.dynamic = None
        self.waiting = None
        self.status.set(reason)

    def record(self, callback, width=24, height=24):
        self.stop("4초 안에 대상 버튼 위에 마우스를 놓으세요.")
        self.root.iconify()

        def finish():
            position = W.POINT()
            U.GetCursorPos(C.byref(position))
            try:
                if not game_at(position.x, position.y):
                    self.status.set("등록 실패 · 포인터 아래에서 PC FISH 버튼을 찾지 못했습니다.")
                    return
                snapshot = capture(position.x, position.y, width, height)
                callback([position.x, position.y, base64.b64encode(snapshot).decode("ascii")])
            except OSError as error:
                self.status.set(str(error))
            finally:
                self.root.deiconify()

        self.root.after(4000, finish)

    def record_step(self):
        def add(step):
            self.steps.append(step)
            self.status.set(f"교배 단계 {len(self.steps)}개 등록됨")
        self.record(add)

    def store_route(self):
        if len(self.steps) < 5:
            self.status.set("교배 경로에 최소 5단계를 등록하세요.")
            return
        self.stop("교배 경로 저장 완료")
        self.routes.append({"name": self.route_name.get().strip() or "교배 경로", "steps": self.steps})
        self.steps = []
        self.refresh_routes()
        self.save()

    def save_scan_order(self, _event):
        self.data["scan_order"] = self.scan_order.current()
        self.save()

    def record_scan(self):
        key = ("slot1", "slot2", "grid", "breed")[self.calibration.current()]
        self.record_action(key)

    def record_badge(self):
        index = self.badge.current() - 1
        rarity, stars = (0, 0) if index < 0 else (index // 5 + 1, index % 5 + 1)

        def store(step):
            self.data.setdefault("badges", {})[f"{rarity},{stars}"] = step[2]
            self.save()
            self.status.set(f"{RARITIES[rarity]} {stars}성 후보 등록 완료")

        self.record(store, 42, 24)

    def candidate(self, grid):
        origin_x, origin_y, _ = grid
        samples = {base64.b64decode(value): tuple(map(int, key.split(",")))
                   for key, value in self.data.get("badges", {}).items()}
        found = []
        for row in range(4):
            for column in range(3):
                x, y = origin_x + 92 * column, origin_y + 92 * row
                if not game_at(x, y):
                    continue
                rating = match_badge(capture(x, y + 27, 52, 34), samples)
                if rating:
                    found.append((*rating, x, y))
        if not found:
            return None
        order = self.data.get("scan_order", 0)
        rank = (lambda row: (row[0], row[1])) if order < 2 else (lambda row: (row[1], row[0]))
        _, _, x, y = sorted(found, key=rank, reverse=order % 2 == 0)[0]
        return [x, y, base64.b64encode(capture(x, y)).decode("ascii")]

    def scan_step(self, state, now):
        phase = self.dynamic
        template = None
        if phase in ("parent1", "parent2"):
            template = self.candidate(self.data["grid"])
        elif phase == "slot2":
            template = self.data["slot2"]
        elif phase == "confirm":
            template = self.data["breed"]
        if template and click(template):
            self.last_action = now
            self.deadline = now + 5
            self.dynamic = {"parent1": "slot2", "slot2": "parent2",
                            "parent2": "confirm", "confirm": None}[phase]
            if phase == "confirm":
                self.waiting = ("breed", state)
                self.deadline = now + 15
        elif now > self.deadline:
            self.stop("일치하는 후보 또는 교배 버튼을 찾지 못해 중지했습니다.")

    def record_upgrade(self):
        self.record_action("upgrade")

    def record_action(self, name):
        def store(step):
            self.data[name] = step
            self.save()
            self.status.set("화면 버튼 등록 완료")
        self.record(store)

    def tick(self):
        try:
            self.sync()
            self.automate()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.stop(f"자동화 중지 · {error}")
        finally:
            self.root.after(200, self.tick)

    def automate(self):
        if not any(flag.get() for flag in self.running):
            return
        if U.GetAsyncKeyState(0x77) & 0x8000:
            self.stop("F8 · 자동화 꺼짐")
            return
        state = self.synced_state
        if state is None:
            self.status.set("게임 저장 데이터 대기 중")
            return
        now = time.monotonic()
        if now - self.last_action < 0.8:
            return
        if self.waiting:
            action, previous_state = self.waiting
            if action == "upgrade":
                complete = state["tankLevel"] > previous_state["tankLevel"]
            else:
                complete = (state["breedCount"] < previous_state["breedCount"]
                            or len(state["fishList"]) > len(previous_state["fishList"]))
            if complete:
                dismiss = self.data.get("dismiss")
                if action == "breed" and dismiss:
                    if click(dismiss):
                        self.last_action = now
                    else:
                        slot = self.data.get("slot1")
                        if now <= self.deadline and (not slot or not matches(slot)):
                            return
                self.waiting = None
                self.status.set("게임 저장에서 완료 확인 · 다음 조건 대기")
            elif now > self.deadline:
                self.stop("게임 저장에서 처리 결과를 확인하지 못해 중지했습니다.")
            return
        if self.dynamic:
            if state["breedCount"] <= 0:
                self.stop("교배 가능 횟수가 없어 중지했습니다.")
                return
            self.scan_step(state, now)
            return
        if self.pending:
            if not self.running[0].get() or state["breedCount"] <= 0:
                self.stop("교배 가능 횟수가 없어 중지했습니다.")
                return
            if click(self.pending[0]):
                self.pending.pop(0)
                self.last_action = now
                self.deadline = now + 4
                if not self.pending:
                    self.waiting = ("breed", state)
                    self.deadline = now + 15
            elif now > self.deadline:
                self.stop("예상한 교배 화면이 표시되지 않아 중지했습니다.")
            return
        upgrade = self.data.get("upgrade")
        if self.running[1].get() and upgrade and self.game.can_upgrade(state):
            if click(upgrade):
                self.waiting = ("upgrade", state)
                self.last_action = now
                self.deadline = now + 15
            else:
                self.open_tab("tank_tab", now)
            return
        if self.running[0].get() and state["breedCount"] > 0:
            if all(self.data.get(key) for key in ("slot1", "slot2", "grid", "breed", "badges")):
                if click(self.data["slot1"]):
                    self.dynamic = "parent1"
                    self.last_action = now
                    self.deadline = now + 5
                    return
            for route in self.routes:
                first, *remaining = route["steps"]
                if click(first):
                    self.pending = remaining
                    self.last_action = now
                    self.deadline = now + 4
                    return
            self.open_tab("breed_tab", now)

    def open_tab(self, name, now):
        template = self.data.get(name)
        if template and click(template):
            self.last_action = now
        else:
            self.status.set("등록된 게임 화면 대기 중")

    def save_fish(self):
        if self.mergeable is not None:
            self.sync_status.set("게임 동기화 중 · 수량은 게임에서 읽습니다.")
            return
        name = self.name.get().strip()
        try:
            stars, amount = int(self.stars.get()), int(self.amount.get())
            if not name or not 1 <= stars <= 5 or not 0 <= amount <= 999:
                raise ValueError
        except ValueError:
            messagebox.showerror("입력 확인", "어종 이름, 1~5성, 0~999마리를 입력하세요.")
            return
        key = [name, self.rarity.current(), stars]
        for row in self.fish:
            if row[:3] == key:
                row[3] = amount
                break
        else:
            self.fish.append([*key, amount])
        self.save()
        self.materials.clear()
        self.refresh_inventory()

    def load_fish(self, _event):
        choice = self.inventory.curselection()
        if not choice:
            return
        name, rarity, stars, amount = self.fish[self.inventory_rows[choice[0]]]
        self.name.delete(0, tk.END)
        self.name.insert(0, name)
        self.rarity.current(rarity)
        self.stars.set(stars or 1)
        self.amount.set(amount)

    def refresh_inventory(self):
        self.inventory.delete(0, tk.END)
        self.inventory_rows = []
        for index, (name, rarity, stars, amount) in enumerate(self.fish):
            if self.owned_only.get() and not amount:
                continue
            self.inventory_rows.append(index)
            grade = f"{stars}성" if stars else "별 등급 없음"
            self.inventory.insert(tk.END, f"{name}   {RARITIES[rarity]}   {grade}   {amount}마리")
        self.refresh_merge()

    def refresh_merge(self):
        self.candidates.delete(0, tk.END)
        self.visible = []
        rarities, stars_filter = self.filters
        for i, (name, rarity, stars, amount) in enumerate(self.fish):
            if amount and rarities[rarity].get() and (stars == 0 or stars_filter[stars - 1].get()):
                self.visible.append(i)
                note = " · 합성 불가" if rarity == 0 else ""
                self.candidates.insert(tk.END, f"{name} · {RARITIES[rarity]} · {stars}성 · {amount}마리{note}")
        self.show_result()

    def show_recipe(self):
        selected = self.season.current() - 1
        if selected < 0 or not self.game:
            self.recipe_summary.set("")
            return
        key = sorted(self.game.recipes)[selected]
        inventory = self.mergeable or {}
        items = []
        for (name, stars), need in self.game.recipes[key].items():
            have = sum(qty for (fish, _, grade), qty in inventory.items()
                       if fish == name and grade == stars)
            items.append(f"{name} {stars}성 {have}/{need} (부족 {max(0, need - have)})")
        self.recipe_summary.set(" · ".join(items))

    def clear_materials(self):
        self.materials.clear()
        self.show_result()

    def add_material(self):
        choice = self.candidates.curselection()
        if not choice:
            return
        i = self.visible[choice[0]]
        name, rarity, stars, available = self.fish[i]
        if self.mergeable is not None:
            available = self.mergeable.get((name, rarity, stars), 0)
        try:
            quantity = int(self.quantity.get())
            if (not rarity or quantity <= 0 or self.materials.get(i, 0) + quantity > available
                    or sum(self.materials.values()) + quantity > 10):
                raise ValueError
        except ValueError:
            messagebox.showerror("재료 확인", f"{name}의 보유량과 합성 재료 한도(10마리)를 확인하세요.")
            return
        self.materials[i] = self.materials.get(i, 0) + quantity
        self.show_result()

    def show_result(self):
        levels = [self.fish[i][1] for i, qty in self.materials.items() for _ in range(qty)]
        text = f"선택한 재료 {len(levels)}/10"
        if self.materials:
            text += "  ·  " + ", ".join(f"{self.fish[i][0]} ×{qty}" for i, qty in self.materials.items())
        if len(levels) == 10:
            text += "\n예상 희귀도   " + " / ".join(
                f"{RARITIES[level]} {chance}%" for level, chance in merge_distribution(levels))
        self.result.set(text)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        assert merge_distribution([2] * 7 + [3] * 3) == [(3, 70), (4, 30)]
        assert merge_distribution([5] * 10) == [(5, 100)]
        assert merge_distribution([1] * 10) == [(2, 100)]
        scan = bytes((index * 31 + index // 20) % 256 for index in range(52 * 34 * 4))
        patch = b"".join(scan[i:i + 168] for i in range(420, 420 + 208 * 24, 208))
        assert match_badge(scan, {patch: (4, 2)}) == (4, 2)
        assert match_badge(scan, {bytes(42 * 24 * 4): (4, 2)}) is None
        print("OK")
    else:
        try:
            U.SetProcessDPIAware()
        except AttributeError:
            pass
        root = tk.Tk()
        Helper(root)
        root.mainloop()
