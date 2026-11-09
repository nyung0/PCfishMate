import ctypes as C
from ctypes import wintypes as W
import base64
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk

from pc_fish_data import GameData

from ocean_ui import CardGrid, OCEAN, PANEL, RARITIES, TEXT, ocean_style
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
foreground_window = api(U, "GetForegroundWindow", W.HWND)
window_rect = api(U, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))


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


def scrolled_badges(frame, samples):
    """Yield registered badge centers in a bottom-up 52-pixel-wide strip."""
    stride = 52 * 4
    for badge, rating in samples.items():
        first_row = badge[:168]
        offset = frame.find(first_row)
        while offset >= 0:
            row, left = divmod(offset, stride)
            if left <= 40 and left % 4 == 0 and row + 24 <= len(frame) // stride:
                if all(frame[offset + i * stride:offset + i * stride + 168]
                       == badge[i * 168:(i + 1) * 168] for i in range(1, 24)):
                    yield len(frame) // stride - row - 12, rating
            offset = frame.find(first_row, offset + 4)


def merge_card_centers(strip, sample):
    stride, width, height = 54 * 4, 44 * 4, 58
    first_row = sample[:width]
    offset = strip.find(first_row)
    while offset >= 0:
        row, left = divmod(offset, stride)
        if left <= stride - width and left % 4 == 0 and row + height <= len(strip) // stride:
            if all(strip[offset + i * stride:offset + i * stride + width]
                   == sample[i * width:(i + 1) * width] for i in range(1, height)):
                yield left // 4 - 5, len(strip) // stride - row - height // 2 - 14
        offset = strip.find(first_row, offset + 4)


def cyan_pixels(frame):
    return [i for i in range(0, len(frame), 4)
            if frame[i] >= 170 and frame[i + 1] >= 135 and frame[i + 2] <= 65]


def locate_sample(frame, width, height, sample, sw, sh):
    stride, segment = width * 4, sw * 4
    first = sample[:segment]
    offset = frame.find(first)
    while offset >= 0:
        row, left = divmod(offset, stride)
        if (left % 4 == 0 and left + segment <= stride and row + sh <= height
                and all(frame[offset + i * stride:offset + i * stride + segment]
                        == sample[i * segment:(i + 1) * segment] for i in range(1, sh))):
            yield left // 4 + sw // 2, height - row - sh // 2
        offset = frame.find(first, offset + 1)


def game_frame():
    rect = W.RECT()
    if not window_rect(foreground_window(), C.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not (30 <= width <= 7680 and 30 <= height <= 4320):
        return None
    x, y = rect.left + width // 2, rect.top + height // 2
    if not game_at(x, y):
        return None
    return capture(x, y, width, height), width, height, rect.left, rect.top


def has_merge_lock(frame):
    return sum(frame[i + 2] <= 20 and 140 <= frame[i] <= 175
               and 140 <= frame[i + 1] <= 175
               for i in range(0, len(frame), 4)) >= 20


SPRITES = Path(__file__).resolve().parent / "fish_sprites"
BREED_REFERENCE = SPRITES.with_name("breed_reference.json")


def catalog_key(row, mode, remaining):
    name, rarity, stars, _ = row
    return (
        (name, -rarity, -stars),
        (-rarity, -stars, name),
        (-stars, -rarity, name),
        (-remaining.get((name, rarity, stars), 0), -rarity, -stars, name),
    )[mode]


class Helper:
    def __init__(self, root):
        self.root = root
        self.data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
        self.fish = self.data.setdefault("fish", [])
        self.game = None
        self.synced_state = None
        self.remaining = {}
        self.art = {}
        self.images = {}
        self.recipe_view = None
        self.merge_busy = False
        self.next_sync = 0
        self.waiting = None
        self.dynamic = None
        self.scan_frame = None
        self.scroll_steps = 0
        self.breed_reference = json.loads(BREED_REFERENCE.read_text(encoding="utf-8"))
        self.running = [tk.BooleanVar(), tk.BooleanVar()]
        self.enabled = [tk.BooleanVar(), tk.BooleanVar()]
        self.last_action = 0
        self.next_breed = 0
        self.breed_interval = 10
        self.deadline = 0
        self.status = tk.StringVar(value="자동화 꺼짐")
        root.title("PC FISH 보조 도구")
        root.geometry("900x660")
        root.minsize(800, 590)
        ocean_style(root)
        masthead = tk.Frame(root, background="#0a3452", padx=22, pady=12)
        masthead.pack(fill="x")
        tk.Label(masthead, text="PC FISH  /  바다 도감", background="#0a3452",
                 foreground=TEXT, font=("Malgun Gothic", 17, "bold")).pack(anchor="w")
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        auto, inventory, merge = (ttk.Frame(tabs, padding=12) for _ in range(3))
        for tab, name in zip((auto, inventory, merge), ("자동화", "도감 · 보유", "시즌 합성 재료")):
            tabs.add(tab, text=name)
        modes = ttk.Frame(auto)
        modes.pack(fill="x")
        for flag, name in zip(self.enabled, ("자동 교배", "자동 수조 레벨업")):
            ttk.Checkbutton(modes, text=name, variable=flag,
                            style="Ocean.TCheckbutton").pack(side="left", padx=(0, 14))
        grades = ttk.Frame(auto)
        grades.pack(fill="x", pady=4)
        ttk.Label(grades, text="교배 허용 희귀도").pack(side="left", padx=(0, 8))
        selected = self.data.get("breed_rarities", list(range(len(RARITIES))))
        self.breed_rarities = [tk.BooleanVar(value=i in selected) for i in range(len(RARITIES))]
        for name, flag in zip(RARITIES, self.breed_rarities):
            ttk.Checkbutton(grades, text=name, variable=flag, style="Ocean.TCheckbutton",
                            command=self.save_breed_rarities).pack(side="left", padx=4)
        controls = ttk.Frame(auto)
        controls.pack(fill="x", pady=(2, 0))
        ttk.Button(controls, text="자동화 시작", command=self.start_automation).pack(side="left")
        ttk.Button(controls, text="중지", command=lambda: self.stop("자동화 꺼짐")).pack(side="left", padx=6)
        ttk.Label(auto, textvariable=self.status).pack(anchor="w", pady=8)
        interval_row = ttk.Frame(auto)
        interval_row.pack(fill="x", pady=(4, 0))
        ttk.Label(interval_row, text="교배 간격 (초)").pack(side="left")
        self.interval_value = tk.StringVar(value=str(self.data.get("breed_interval", 10)))
        ttk.Spinbox(interval_row, from_=1, to=3600, width=7,
                    textvariable=self.interval_value).pack(side="left", padx=8)
        ttk.Label(interval_row, text="우선순위  희귀도 높은 순 → 별 등급 높은 순").pack(side="left")
        ttk.Label(auto, text="하트가 생기면 교배합니다. F8을 누르면 자동화가 멈춥니다.").pack(anchor="w", pady=8)
        extra = ttk.Frame(auto)
        extra.pack(fill="x", pady=8)
        ttk.Button(extra, text="교배 결과 닫기 등록",
                   command=lambda: self.record_action("dismiss")).pack(side="left")
        ttk.Button(extra, text="수조 탭 등록",
                   command=lambda: self.record_action("tank_tab")).pack(side="left")
        ttk.Button(extra, text="교배 탭 등록",
                   command=lambda: self.record_action("breed_tab")).pack(side="left")
        ttk.Button(extra, text="수조 버튼 등록", command=self.record_upgrade).pack(side="left")
        ttk.Label(auto, text="자동 교배 시작 후 게임의 교배 탭을 앞에 두세요. · 한국어 UI 1배율").pack(anchor="w")

        toolbar = ttk.Frame(inventory)
        toolbar.pack(fill="x")
        self.sync_status = tk.StringVar(value="로컬 도감 읽는 중")
        ttk.Label(toolbar, textvariable=self.sync_status).pack(side="left")
        self.sort_order = ttk.Combobox(toolbar, state="readonly", width=14,
            values=("이름순", "희귀도순", "별 등급순", "남은 교배순"))
        self.sort_order.current(0)
        self.sort_order.bind("<<ComboboxSelected>>", lambda _event: self.refresh_inventory())
        self.sort_order.pack(side="right")
        self.owned_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="보유한 어종만", variable=self.owned_only,
                        command=self.refresh_inventory).pack(side="right", padx=12)
        merge_bar = ttk.Frame(inventory)
        merge_bar.pack(fill="x", pady=(8, 0))
        self.merge_status = tk.StringVar(value="일반 합성 · 보낼 물고기의 도감 카드를 클릭하세요.")
        ttk.Button(merge_bar, text="후보 목록 제목 등록",
                   command=lambda: self.record_merge_anchor("merge_header")).pack(side="left")
        ttk.Button(merge_bar, text="첫 빈 슬롯 등록",
                   command=lambda: self.record_merge_anchor("merge_slot")).pack(side="left", padx=6)
        self.merge_register = tk.BooleanVar()
        ttk.Checkbutton(merge_bar, text="후보 카드 등록", variable=self.merge_register).pack(side="left")
        ttk.Label(inventory, textvariable=self.merge_status, wraplength=740).pack(anchor="w")
        self.inventory = CardGrid(inventory, self.select_merge_card)
        self.inventory.pack(fill="both", expand=True, pady=(10, 0))

        self.season = ttk.Combobox(merge, state="readonly")
        self.season.pack(fill="x")
        self.season.bind("<<ComboboxSelected>>", lambda _event: self.show_recipe())
        self.recipe_summary = tk.StringVar(value="게임 데이터에서 시즌 레시피를 읽는 중")
        ttk.Label(merge, textvariable=self.recipe_summary).pack(anchor="w", pady=8)
        self.recipe_cards = CardGrid(merge)
        self.recipe_cards.pack(fill="both", expand=True)
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
                for variant, (species, rarity, stars) in sorted(self.game.variants.items()):
                    name = self.game.names.get(species, species)
                    self.art.setdefault((name, rarity, stars), variant)
            state = self.game.state()
            if state is not self.synced_state:
                self.fish, self.remaining = self.game.rows()
                self.refresh_inventory(reset=False)
            self.synced_state = state
            if not self.season.cget("values") and self.game.recipes:
                self.season["values"] = ["시즌 레시피 선택"] + [
                    f"시즌 {season} · {name} · {stars}성"
                    for season, name, stars in sorted(self.game.recipes)]
                self.season.current(1)
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

    def start_automation(self):
        if any(flag.get() for flag in self.running):
            self.status.set("자동화 실행 중 · 설정을 다시 적용하려면 먼저 중지하세요.")
            return
        if not any(flag.get() for flag in self.enabled):
            self.status.set("실행할 자동화 기능을 하나 이상 체크하세요.")
            return
        if self.enabled[0].get() and not self.allowed_breed_rarities():
            self.status.set("교배 허용 희귀도를 하나 이상 체크하세요.")
            return
        if self.enabled[0].get():
            supported = {int(key.split(",", 1)[0])
                         for key in (*self.breed_reference["badges"], *self.data.get("badges", {}))}
            if not self.allowed_breed_rarities() & supported:
                self.status.set("현재 화면 인식이 지원되는 희귀도는 기본과 일반 1성입니다.")
                return
            try:
                seconds = int(self.interval_value.get())
                if not 1 <= seconds <= 3600:
                    raise ValueError
            except ValueError:
                self.status.set("교배 간격은 1~3600초 사이의 정수로 입력하세요.")
                return
            self.breed_interval = seconds
            self.data["breed_interval"] = seconds
            self.save()
        self.next_breed = 0
        self.dynamic = None
        self.waiting = None
        for active, selected in zip(self.running, self.enabled):
            active.set(selected.get())
        self.status.set("자동화 시작 · 게임 화면 대기 중")

    def stop(self, reason):
        for flag in self.running:
            flag.set(False)
        self.dynamic = None
        self.waiting = None
        self.status.set(reason)

    def record(self, callback, width=24, height=24, status=None):
        status = status if status is not None else self.status
        self.stop("4초 안에 대상 버튼 위에 마우스를 놓으세요.")
        status.set("4초 안에 게임의 등록 위치에 마우스를 놓으세요.")
        self.root.iconify()

        def finish():
            position = W.POINT()
            U.GetCursorPos(C.byref(position))
            try:
                if not game_at(position.x, position.y):
                    status.set("등록 실패 · 포인터 아래에서 PC FISH 버튼을 찾지 못했습니다.")
                    return
                snapshot = capture(position.x, position.y, width, height)
                callback([position.x, position.y, base64.b64encode(snapshot).decode("ascii")])
            except OSError as error:
                status.set(str(error))
            finally:
                self.root.deiconify()

        self.root.after(4000, finish)

    def allowed_breed_rarities(self):
        return {i for i, flag in enumerate(self.breed_rarities) if flag.get()}

    def save_breed_rarities(self):
        self.data["breed_rarities"] = sorted(self.allowed_breed_rarities())
        if self.running[0].get():
            self.running[0].set(False)
            self.dynamic = None
        self.save()
        self.status.set("교배 허용 희귀도 저장 · 자동 교배를 다시 켜면 적용됩니다.")

    def find_breed_anchor(self, screen, name, width, height):
        frame, fw, fh, left, top = screen
        sample = base64.b64decode(self.breed_reference[name])
        hits = list(locate_sample(frame, fw, fh, sample, width, height))
        if len(hits) == 1:
            x, y = hits[0]
            if game_at(left + x, top + y):
                return left + x, top + y
        return None

    def breed_slot(self, title, offset):
        x, y = title[0] + offset, title[1] + 117
        return x, y, self.breed_reference["plus"]

    def breed_candidate(self, picker):
        x, y = picker
        allowed = self.allowed_breed_rarities()
        badges = {**self.data.get("badges", {}), **self.breed_reference["badges"]}
        samples = {base64.b64decode(value): tuple(map(int, key.split(",")))
                   for key, value in badges.items() if int(key.split(",", 1)[0]) in allowed}
        strips = tuple(capture(x + col * 92, y + 234, 52, 352) for col in (-1, 0, 1))
        found = []
        for col, strip in zip((-1, 0, 1), strips):
            px = x + col * 92
            for badge_y, rating in scrolled_badges(strip, samples):
                py = y + 58 + badge_y - 30
                if (y + 90 <= py <= y + 385 and game_at(px, py)
                        and not has_merge_lock(capture(px - 30, py - 28, 18, 18))):
                    found.append((*rating, px, py))
        if found:
            _, _, px, py = max(found, key=lambda item: (item[0], item[1]))
            return (px, py, base64.b64encode(capture(px, py)).decode()), strips
        return None, strips

    def scan_step(self, state, now):
        phase = self.dynamic
        template = None
        screen = game_frame()
        title = self.find_breed_anchor(screen, "title", 30, 16) if screen else None
        if title and phase in ("parent1", "parent2"):
            picker = (self.find_breed_anchor(screen, "picker", 70, 16)
                      or self.find_breed_anchor(screen, "picker_filled", 70, 16))
            if picker:
                template, strips = self.breed_candidate(picker)
                if not template and self.scan_frame != strips and self.scroll_steps < 40:
                    x, y = picker[0], picker[1] + 210
                    if game_at(x, y) and U.SetCursorPos(x, y) and game_at(x, y):
                        U.mouse_event(0x0800, 0, 0, C.c_ulong(-120), 0)
                        self.scan_frame = strips
                        self.scroll_steps += 1
                        self.last_action = now
                        self.deadline = now + 5
                        return
        elif title and phase == "slot2" and not matches(self.breed_slot(title, -95)):
            template = self.breed_slot(title, 95)
        elif title and phase == "confirm" and not any(
                matches(self.breed_slot(title, offset)) for offset in (-95, 95)):
            template = title[0], title[1] + 428, self.breed_reference["confirm"]
        if template and click(template):
            self.last_action = now
            self.deadline = now + 5
            self.scan_frame = None
            self.scroll_steps = 0
            self.dynamic = {"parent1": "slot2", "slot2": "parent2",
                            "parent2": "confirm", "confirm": None}[phase]
            if phase == "confirm":
                self.waiting = ("breed", state)
                self.next_breed = now + self.breed_interval
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
        upgrade = self.data.get("upgrade")
        if self.running[1].get() and upgrade and self.game.can_upgrade(state):
            if click(upgrade):
                self.waiting = ("upgrade", state)
                self.last_action = now
                self.deadline = now + 15
            else:
                self.open_tab("tank_tab", now)
            return
        if self.running[0].get():
            if state["breedCount"] <= 0:
                self.status.set("교배 하트가 생기기를 기다리는 중")
                return
            if now < self.next_breed:
                self.status.set(f"다음 교배까지 {int(self.next_breed - now) + 1}초")
                return
            screen = game_frame()
            title = self.find_breed_anchor(screen, "title", 30, 16) if screen else None
            if title and matches(self.breed_slot(title, 95)) and click(self.breed_slot(title, -95)):
                self.scan_frame = None
                self.scroll_steps = 0
                self.dynamic = "parent1"
                self.last_action = now
                self.deadline = now + 5
                return
            self.status.set("게임의 교배 탭과 빈 부모 슬롯을 기다리는 중")

    def open_tab(self, name, now):
        template = self.data.get(name)
        if template and click(template):
            self.last_action = now
        else:
            self.status.set("등록된 게임 화면 대기 중")

    def picture(self, name, rarity, stars, owned):
        variant = self.art.get((name, rarity, stars))
        if variant is None:
            return None
        key = (variant, owned)
        if key not in self.images:
            filename = f"{variant}{'' if owned else '-gray'}.png"
            path = SPRITES / filename
            self.images[key] = tk.PhotoImage(file=str(path)) if path.is_file() else None
        return self.images[key]

    def record_merge_anchor(self, name):
        def store(step):
            if name == "merge_slot" and len(cyan_pixels(base64.b64decode(step[2]))) < 20:
                self.merge_status.set("등록 실패 · 첫 번째 빈 슬롯의 파란 + 중앙을 등록하세요.")
                return
            self.data[name] = step
            self.save()
            self.merge_status.set("일반 합성 위치를 저장했습니다.")

        self.record(store, status=self.merge_status)

    def select_merge_card(self, card):
        name, rarity, stars, _, _, owned = card
        if self.merge_busy or not owned:
            return
        key = json.dumps([name, rarity, stars], ensure_ascii=False)
        if self.merge_register.get():
            self.record_merge_sample(key, name)
            return
        header, slot = (self.data.get(k) for k in ("merge_header", "merge_slot"))
        sample = self.data.get("merge_samples", {}).get(key)
        if not header or not slot or not sample:
            self.merge_status.set("일반 합성 위치와 이 물고기의 후보 카드를 먼저 등록하세요.")
            return
        if any(flag.get() for flag in self.running):
            self.merge_status.set("자동 교배와 수조 레벨업을 끈 뒤 다시 클릭하세요.")
            return
        self.merge_busy = True
        self.root.iconify()
        self.root.after(350, lambda: self.send_merge_card(header, slot, sample))

    def record_merge_sample(self, key, name):
        header = self.data.get("merge_header")
        if not header:
            self.merge_status.set("먼저 후보 목록 제목을 등록하세요.")
            return

        def store(step):
            x, y, _ = step
            if not matches(header) or not self.merge_candidate_position(header, x, y):
                self.merge_status.set("등록 실패 · 일반 합성 후보의 물고기 그림 중앙을 등록하세요.")
                return
            if has_merge_lock(capture(x - 30, y - 28, 18, 18)):
                self.merge_status.set("등록 실패 · 잠금 표시가 없는 후보를 등록하세요.")
                return
            sample = base64.b64encode(capture(x, y + 14, 44, 58)).decode("ascii")
            samples = self.data.setdefault("merge_samples", {})
            if any(other != key and value == sample for other, value in samples.items()):
                self.merge_status.set("등록 실패 · 다른 도감 카드와 화면 표본이 같습니다.")
                return
            samples[key] = sample
            self.save()
            self.merge_register.set(False)
            self.merge_status.set(f"{name} 후보를 저장했습니다. 카드를 클릭하면 재료로 추가합니다.")

        self.record(store, status=self.merge_status)

    @staticmethod
    def merge_candidate_position(header, x, y):
        hx, hy, _ = header
        return (min(abs(x - hx - 92 * col) for col in (-1, 0, 1)) <= 5
                and hy + 83 <= y <= hy + 380)

    @staticmethod
    def merge_empty_slots(slot):
        x, y, encoded = slot
        reference = cyan_pixels(base64.b64decode(encoded))
        positions = [(x + 92 * col, y + 92 * row) for row in range(2) for col in range(4)]
        positions.extend((x + 92 * col, y + 184) for col in (1, 2))
        if not reference or not all(game_at(px, py) for px, py in positions):
            return None
        empty = set()
        for index, (px, py) in enumerate(positions):
            actual = set(cyan_pixels(capture(px, py)))
            offsets = (4 * (24 * dy + dx) for dy in (-2, -1, 0, 1, 2) for dx in (-1, 0, 1))
            if any(sum(i + offset in actual for i in reference) >= len(reference) * 0.85
                   for offset in offsets):
                empty.add(index)
        return empty

    def finish_merge(self, message):
        self.merge_busy = False
        self.merge_status.set(message)
        self.root.deiconify()

    def send_merge_card(self, header, slot, encoded):
        try:
            if not matches(header):
                self.finish_merge("일반 합성과 물고기 선택 창을 등록한 위치에 열어주세요.")
                return
            before = self.merge_empty_slots(slot)
            if not before:
                self.finish_merge("빈 재료 슬롯을 찾지 못했습니다.")
                return
            sample = base64.b64decode(encoded)
            hx, hy, _ = header
            for col in (-1, 0, 1):
                x = hx + 92 * col
                strip = capture(x, hy + 243, 54, 366)
                for dx, dy in merge_card_centers(strip, sample):
                    px, py = x + dx, hy + 60 + dy
                    if not self.merge_candidate_position(header, px, py) or not game_at(px, py):
                        continue
                    if (capture(px, py + 14, 44, 58) != sample
                            or has_merge_lock(capture(px - 30, py - 28, 18, 18))):
                        continue
                    template = [px, py, base64.b64encode(capture(px, py)).decode("ascii")]
                    if matches(header) and click(template):
                        self.root.after(400, lambda: self.verify_merge_card(header, slot, before))
                        return
            self.finish_merge("일치하는 후보가 보이지 않습니다. 목록을 옮기거나 카드 표본을 다시 등록하세요.")
        except (OSError, ValueError) as error:
            self.finish_merge(f"재료 추가 중지 · {error}")

    def verify_merge_card(self, header, slot, before):
        try:
            after = self.merge_empty_slots(slot)
            if after is not None and after < before and len(before - after) == 1:
                self.finish_merge("일반 합성 재료에 물고기 1마리를 추가했습니다.")
                return
            self.finish_merge("재료 슬롯 변경을 확인하지 못했습니다. 게임 화면에서 확인하세요.")
        except OSError as error:
            self.finish_merge(f"재료 확인 중지 · {error}")

    def refresh_inventory(self, reset=True):
        rows = sorted(self.fish, key=lambda row: catalog_key(
            row, self.sort_order.current(), self.remaining))
        cards = []
        for name, rarity, stars, amount in rows:
            if self.owned_only.get() and not amount:
                continue
            left = self.remaining.get((name, rarity, stars), 0)
            detail = f"{amount}마리 · 교배 {left}회"
            picture = self.picture(name, rarity, stars, bool(amount))
            cards.append((name, rarity, stars, detail, picture, bool(amount)))
        self.inventory.set_cards(cards, reset)

    def show_recipe(self):
        selected = self.season.current() - 1
        if selected < 0 or not self.game:
            return
        key = sorted(self.game.recipes)[selected]
        owned = {(name, rarity, stars): amount for name, rarity, stars, amount in self.fish}
        items = []
        for (name, stars), need in self.game.recipes[key].items():
            grades = [rarity for fish, rarity, grade in self.art if fish == name and grade == stars]
            rarity = max(grades, default=0)
            have = sum(qty for (fish, _, grade), qty in owned.items()
                       if fish == name and grade == stars)
            items.append((name, rarity, stars, min(have, need), need))
        view = (key, items)
        if view == self.recipe_view:
            return
        reset = self.recipe_view is None or self.recipe_view[0] != key
        self.recipe_view = view
        available = sum(have for _, _, _, have, _ in items)
        total = sum(need for _, _, _, _, need in items)
        self.recipe_summary.set(f"보유 재료 {available} / {total}마리   ·   컬러 보유   /   흑백 미보유")
        cards = []
        for name, rarity, stars, have, need in items:
            for slot in range(need):
                present = slot < have
                cards.append((name, rarity, stars, "보유" if present else "부족",
                              self.picture(name, rarity, stars, present), present))
        self.recipe_cards.set_cards(cards, reset)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        scan = bytes((index * 31 + index // 20) % 256 for index in range(52 * 34 * 4))
        patch = b"".join(scan[i:i + 168] for i in range(420, 420 + 208 * 24, 208))
        assert match_badge(scan, {patch: (4, 2)}) == (4, 2)
        assert match_badge(scan, {bytes(42 * 24 * 4): (4, 2)}) is None
        assert list(scrolled_badges(scan, {patch: (4, 2)})) == [(20, (4, 2))]
        print("OK")
    else:
        try:
            U.SetProcessDPIAware()
        except AttributeError:
            pass
        root = tk.Tk()
        Helper(root)
        root.mainloop()
