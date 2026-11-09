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
        self.routes = self.data.setdefault("routes", [])
        self.running = [tk.BooleanVar(), tk.BooleanVar()]
        self.steps = []
        self.pending = []
        self.last_action = 0
        self.deadline = 0
        self.status = tk.StringVar(value="자동화 꺼짐")
        root.title("PC FISH 보조 도구")
        root.geometry("760x530")
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
        ttk.Label(inventory, text="어종 이름 · 희귀도 · 별 등급 · 현재 보유 수량").pack(anchor="w")
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
        self.refresh_inventory()
        self.root.after(200, self.tick)

    def save(self):
        DATA.parent.mkdir(parents=True, exist_ok=True)
        temporary = DATA.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(DATA)

    def toggle(self):
        self.pending.clear()
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
        self.status.set(reason)

    def record(self, callback):
        self.stop("4초 안에 대상 버튼 위에 마우스를 놓으세요.")
        self.root.iconify()

        def finish():
            position = W.POINT()
            U.GetCursorPos(C.byref(position))
            try:
                if not game_at(position.x, position.y):
                    self.status.set("등록 실패 · 포인터 아래에서 PC FISH 버튼을 찾지 못했습니다.")
                    return
                snapshot = capture(position.x, position.y)
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

    def record_upgrade(self):
        def set_upgrade(step):
            self.data["upgrade"] = step
            self.save()
            self.status.set("수조 레벨업 버튼 등록 완료")
        self.record(set_upgrade)

    def tick(self):
        try:
            self.automate()
        except (OSError, ValueError) as error:
            self.stop(f"자동화 중지 · {error}")
        finally:
            self.root.after(200, self.tick)

    def automate(self):
        if not any(flag.get() for flag in self.running):
            return
        if U.GetAsyncKeyState(0x77) & 0x8000:
            self.stop("F8 · 자동화 꺼짐")
            return
        now = time.monotonic()
        if now - self.last_action < 0.8:
            return
        if self.pending:
            if click(self.pending[0]):
                self.pending.pop(0)
                self.last_action = now
                self.deadline = now + 4
                if not self.pending:
                    self.stop("교배 입력 완료 · 결과 확인 후 다시 켜세요.")
            elif now > self.deadline:
                self.stop("예상한 게임 화면이 표시되지 않아 자동화를 중지했습니다.")
            return
        upgrade = self.data.get("upgrade")
        if self.running[1].get() and upgrade and click(upgrade):
            self.stop("수조 레벨업 입력 완료 · 결과 확인 후 다시 켜세요.")
            return
        if self.running[0].get():
            for route in self.routes:
                first, *remaining = route["steps"]
                if click(first):
                    self.pending = remaining
                    self.last_action = now
                    self.deadline = now + 4
                    return

    def save_fish(self):
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
        name, rarity, stars, amount = self.fish[choice[0]]
        self.name.delete(0, tk.END)
        self.name.insert(0, name)
        self.rarity.current(rarity)
        self.stars.set(stars)
        self.amount.set(amount)

    def refresh_inventory(self):
        self.inventory.delete(0, tk.END)
        for name, rarity, stars, amount in self.fish:
            self.inventory.insert(tk.END, f"{name}   {RARITIES[rarity]}   {stars}성   {amount}마리")
        self.refresh_merge()

    def refresh_merge(self):
        self.candidates.delete(0, tk.END)
        self.visible = []
        rarities, stars_filter = self.filters
        for i, (name, rarity, stars, amount) in enumerate(self.fish):
            if amount and rarities[rarity].get() and stars_filter[stars - 1].get():
                self.visible.append(i)
                note = " · 합성 불가" if rarity == 0 else ""
                self.candidates.insert(tk.END, f"{name} · {RARITIES[rarity]} · {stars}성 · {amount}마리{note}")
        self.show_result()

    def clear_materials(self):
        self.materials.clear()
        self.show_result()

    def add_material(self):
        choice = self.candidates.curselection()
        if not choice:
            return
        i = self.visible[choice[0]]
        name, rarity, _, available = self.fish[i]
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
        print("OK")
    else:
        try:
            U.SetProcessDPIAware()
        except AttributeError:
            pass
        root = tk.Tk()
        Helper(root)
        root.mainloop()
