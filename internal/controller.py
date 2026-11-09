import json
import os
import ctypes
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import zipfile
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4
from ctypes import wintypes
from ocean_ui import PANEL, RARITIES, TEXT, WATER, ocean_style
from season_tab import GameData, SeasonTab, load_season_art


BRIDGE = Path(os.environ["LOCALAPPDATA"]) / "PCFishHelper" / "internal"
DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\PC FISH")
ASSETS = Path(__file__).resolve().parent
LOADER_FILES = ("BepInEx", "dotnet", ".doorstop_version", "doorstop_config.ini", "winhttp.dll")


def set_taskbar_icon(root, icon):
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SendMessageW.restype = wintypes.LPARAM
    window = user32.GetAncestor(root.winfo_id(), 2)
    if not window:
        return
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    scale = ctypes.c_int()
    monitor = user32.MonitorFromWindow(window, 2)
    if ctypes.windll.shcore.GetScaleFactorForMonitor(ctypes.c_void_p(monitor), ctypes.byref(scale)) == 0:
        dpi = round(96 * scale.value / 100)
    else:
        dpi = user32.GetDpiForWindow(window) or 96
    for kind, width, height in ((0, 49, 50), (1, 11, 12)):
        handle = user32.LoadImageW(None, str(icon), 1,
                                   user32.GetSystemMetricsForDpi(width, dpi),
                                   user32.GetSystemMetricsForDpi(height, dpi), 0x0010)
        if handle:
            user32.SendMessageW(window, 0x0080, kind, handle)


def find_game():
    roots = [DEFAULT_GAME.parents[2]]
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
    libraries = list(roots)
    for root in roots:
        try:
            vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        libraries.extend(Path(path.replace("\\\\", "\\")) for path in re.findall(r'"path"\s+"([^"]+)"', vdf))
    for library in dict.fromkeys(libraries):
        steamapps = library / "steamapps"
        names = ["PC FISH"]
        try:
            manifest = (steamapps / "appmanifest_4842670.acf").read_text(encoding="utf-8-sig")
            match = re.search(r'"installdir"\s+"([^"]+)"', manifest)
            if match:
                names.insert(0, match[1])
        except (OSError, UnicodeError):
            pass
        for name in names:
            game = steamapps / "common" / name
            if (game / "PCFish.exe").is_file() and (game / "GameAssembly.dll").is_file():
                return game
    return DEFAULT_GAME


def install_module(game):
    if not (game / "PCFish.exe").is_file() or not (game / "GameAssembly.dll").is_file():
        raise ValueError("PCFish.exe가 있는 게임 폴더를 선택하세요.")
    processes = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq PCFish.exe", "/NH"],
                                        creationflags=subprocess.CREATE_NO_WINDOW)
    if b"pcfish.exe" in processes.lower():
        raise ValueError("게임을 종료한 뒤 모듈을 설치하세요.")
    present = [name for name in LOADER_FILES if (game / name).exists()]
    if present and len(present) != len(LOADER_FILES):
        raise ValueError("기존 로더 파일 일부가 남아 있어 설치를 중지했습니다.")
    if not present:
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(ASSETS / "loader.zip") as archive:
                archive.extractall(folder)
            for name in LOADER_FILES:
                source = Path(folder) / name
                destination = game / name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
    plugins = game / "BepInEx" / "plugins"
    plugins.mkdir(exist_ok=True)
    shutil.copy2(ASSETS / "PCFishInternal.dll", plugins)
    config = game / "BepInEx" / "config" / "BepInEx.cfg"
    config.parent.mkdir(exist_ok=True)
    if config.exists():
        current = config.read_text(encoding="utf-8-sig")
        updated, count = re.subn(r"(?m)(^\[Logging\.Console\][^\[]*?^Enabled[ \t]*=[ \t]*)(true|false)",
                                 r"\g<1>false", current, count=1)
        if not count:
            updated = current.rstrip() + "\n\n[Logging.Console]\nEnabled = false\n"
        config.write_text(updated, encoding="utf-8")
    else:
        config.write_text("[Logging.Console]\nEnabled = false\n\n[Logging.Disk]\nEnabled = true\n",
                          encoding="utf-8")


def elevated_install(game):
    args = ["--install-module", str(game)]
    if not getattr(sys, "frozen", False):
        args.insert(0, str(Path(__file__).resolve()))
    return ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, subprocess.list2cmdline(args), None, 1)


class Controller:
    def __init__(self, root):
        self.root = root
        self.command = None
        self.run_id = uuid4().hex
        root.title("PC FISH · 바다 도감")
        icon = ASSETS / "pcfish.ico"
        if icon.is_file():
            root.iconbitmap(default=str(icon))
            root.bind("<Map>", lambda event: root.after_idle(
                lambda: set_taskbar_icon(root, icon)) if event.widget is root else None, add="+")
        root.geometry("820x600")
        root.minsize(670, 410)
        root.protocol("WM_DELETE_WINDOW", self.close)
        ocean_style(root)
        header = tk.Label(root, text="PC FISH  /  바다 도감", bg=WATER, fg=TEXT,
                          font=("Malgun Gothic", 17, "bold"), anchor="w", padx=20, pady=12)
        badge = ASSETS / "pcfish-badge.png"
        if badge.is_file():
            self.header_icon = tk.PhotoImage(master=root, file=str(badge))
            header.configure(image=self.header_icon, compound="left", padx=14)
        header.pack(fill="x")
        self.game_path = tk.StringVar(value=str(find_game()))
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        panel = ttk.Frame(tabs, padding=16)
        tabs.add(panel, text="자동화")
        season = SeasonTab(tabs, self.game_path)
        tabs.add(season, text="합성")
        tabs.bind("<<NotebookTabChanged>>", lambda _: season.refresh())
        install = ttk.Frame(panel)
        install.pack(fill="x", pady=(10, 0))
        ttk.Entry(install, textvariable=self.game_path).pack(side="left", fill="x", expand=True)
        ttk.Button(install, text="폴더 선택", command=self.pick_game).pack(side="left", padx=5)
        ttk.Button(install, text="모듈 설치", command=self.install).pack(side="left")
        ttk.Button(install, text="게임 실행",
                   command=lambda: os.startfile("steam://rungameid/4842670")).pack(side="left", padx=(5, 0))
        self.module_status = tk.StringVar()
        ttk.Label(panel, textvariable=self.module_status).pack(anchor="w", pady=(6, 0))
        self.status = tk.StringVar(value="게임 플러그인 연결 대기 중")
        ttk.Label(panel, textvariable=self.status, wraplength=430).pack(anchor="w", pady=8)
        modes = ttk.Frame(panel)
        modes.pack(anchor="w", pady=(0, 6))
        self.breed_enabled = tk.BooleanVar(value=True)
        self.upgrade_enabled = tk.BooleanVar(value=False)
        for label, flag in (("교배 자동화", self.breed_enabled), ("수조 레벨업 자동화", self.upgrade_enabled)):
            ttk.Checkbutton(modes, text=label, variable=flag,
                            style="Ocean.TCheckbutton", command=self.update_modes).pack(side="left", padx=(0, 14))
        grades = ttk.Frame(panel)
        grades.pack(anchor="w")
        self.flags = [tk.BooleanVar(value=True) for _ in RARITIES]
        for grade, flag in zip(RARITIES, self.flags):
            ttk.Checkbutton(grades, text=grade, variable=flag,
                            style="Ocean.TCheckbutton").pack(side="left", padx=(0, 8))
        priority = ttk.Frame(panel)
        priority.pack(anchor="w", pady=(8, 0))
        ttk.Label(priority, text="교배 우선순위").pack(side="left", padx=(0, 8))
        self.priority = ttk.Combobox(priority, state="readonly", width=34,
                                    values=("희귀도 높은 순 → 별 등급 높은 순", "별 등급 높은 순 → 희귀도 높은 순"))
        self.priority.current(0)
        self.priority.pack(side="left")
        controls = ttk.Frame(panel)
        controls.pack(anchor="w", pady=10)
        self.interval = tk.StringVar(value="10")
        ttk.Label(controls, text="교배 간격 (초)").pack(side="left")
        ttk.Spinbox(controls, textvariable=self.interval, width=5, from_=1, to=3600).pack(side="left", padx=6)
        self.start_button = ttk.Button(controls, text="자동화 시작", command=self.start)
        self.start_button.pack(side="left", padx=4)
        ttk.Button(controls, text="중지", command=self.stop).pack(side="left")
        ttk.Button(controls, text="로그 보기", command=self.open_log).pack(side="left", padx=6)
        ttk.Label(panel, text="교배 결과").pack(anchor="w", pady=(6, 4))
        self.history = ttk.Treeview(panel, columns=("parent1", "parent2", "child"), show="headings")
        for column, label in zip(("parent1", "parent2", "child"), ("부모 1", "부모 2", "태어난 물고기")):
            self.history.heading(column, text=label)
            self.history.column(column, width=180, anchor="center")
        self.history.pack(fill="both", expand=True)
        self.log_window = None
        self.last_log = ""
        self.last_history = ()
        self.catalog_path = None
        self.catalog = None
        self.tick()

    def open_log(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("PC FISH · 자동화 로그")
        self.log_window.geometry("740x420")
        logs = ttk.Frame(self.log_window, padding=12)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, height=12, wrap="word", state="disabled",
                           background=PANEL, foreground=TEXT, relief="flat")
        scrollbar = ttk.Scrollbar(logs, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.show_log(self.last_log)

    def show_log(self, text):
        if self.log_window is None or not self.log_window.winfo_exists():
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def fish_label(self, variant):
        if not variant:
            return "확인되지 않음"
        if self.catalog and variant in self.catalog.variants:
            species, rarity, stars = self.catalog.variants[variant]
            name = self.catalog.names.get(species, species)
            grade = f" · {stars}성" if stars else ""
            return f"{name} · {RARITIES[rarity]}{grade}"
        return variant

    def pick_game(self):
        path = filedialog.askdirectory(initialdir=self.game_path.get())
        if path:
            self.game_path.set(path)

    def install(self):
        game = Path(self.game_path.get())
        if not (game / "PCFish.exe").is_file():
            messagebox.showerror("모듈 설치", "PCFish.exe가 있는 폴더를 선택하세요.")
            return
        if elevated_install(game) <= 32:
            messagebox.showerror("모듈 설치", "관리자 권한 설치를 시작하지 못했습니다.")

    def refresh_log(self):
        game = Path(self.game_path.get())
        dll = game / "BepInEx" / "plugins" / "PCFishInternal.dll"
        self.module_status.set("내부 모듈 설치됨" if dll.is_file() else "내부 모듈 설치 필요")
        path = game / "BepInEx" / "LogOutput.log"
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 48_000))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
            useful = [line for line in lines if "PC Fish Internal Breeding" in line
                      or line.startswith(("[Error", "[Fatal"))]
            text = "\n".join(useful[-100:])
        except OSError:
            useful = []
            text = ""
        if text != self.last_log:
            self.last_log = text
            self.show_log(text)
        records = tuple(line.split("BREED_RESULT ", 1)[1] for line in useful if "BREED_RESULT " in line)
        if records == self.last_history and game == self.catalog_path:
            return
        self.last_history = records
        if game != self.catalog_path:
            self.catalog_path, self.catalog = game, None
        if records and self.catalog is None:
            try:
                self.catalog = GameData(install=game)
            except (OSError, ValueError):
                pass
        self.history.delete(*self.history.get_children())
        for record in reversed(records):
            try:
                result = json.loads(record)
                first, second = result["Parents"]
                values = tuple(map(self.fish_label, (first, second, result["Child"])))
            except (KeyError, TypeError, ValueError):
                continue
            self.history.insert("", "end", values=values)

    def write(self, command):
        BRIDGE.mkdir(parents=True, exist_ok=True)
        temporary = BRIDGE / f"{self.run_id}.tmp"
        temporary.write_text(json.dumps(command), encoding="utf-8")
        temporary.replace(BRIDGE / "command.json")

    def start(self):
        selected = [i for i, flag in enumerate(self.flags) if flag.get()]
        breed, upgrade = self.breed_enabled.get(), self.upgrade_enabled.get()
        if not breed and not upgrade:
            self.status.set("실행할 자동화 기능을 하나 이상 선택하세요.")
            return
        try:
            interval = int(self.interval.get()) if breed else 10
            if not 1 <= interval <= 3600 or (breed and not selected):
                raise ValueError
        except ValueError:
            self.status.set("희귀도를 체크하고 1~3600초 사이의 간격을 입력하세요.")
            return
        self.run_id = uuid4().hex
        self.command = {"RunId": self.run_id, "Enabled": True, "Interval": interval, "Rarities": selected,
                        "Breed": breed, "TankUpgrade": upgrade, "StarsFirst": self.priority.current() == 1}
        self.tick_status()

    def stop(self):
        self.command = None
        try:
            self.write({"RunId": self.run_id, "Enabled": False, "Interval": 10, "Rarities": []})
        except OSError as error:
            self.status.set(f"중지 요청 전달 실패 · {error}")

    def update_modes(self):
        if self.command is None:
            return
        breed, upgrade = self.breed_enabled.get(), self.upgrade_enabled.get()
        if not breed and not upgrade:
            self.stop()
            return
        self.command.update(Breed=breed, TankUpgrade=upgrade)
        self.tick_status()

    def close(self):
        self.stop()
        self.root.destroy()

    def tick_status(self):
        try:
            path = BRIDGE / "status.json"
            connected = time.time() - path.stat().st_mtime < 5
            if connected:
                state = json.loads(path.read_text(encoding="utf-8"))
                self.status.set(("실행 중 · " if state["Running"] else "") + state["Message"])
                if self.command:
                    self.write(self.command)
            else:
                self.status.set("게임 플러그인 연결 대기 중")
        except (OSError, ValueError, KeyError):
            connected = False
            self.status.set("게임 플러그인 연결 대기 중")
        self.start_button.state(["!disabled"] if connected else ["disabled"])

    def tick(self):
        self.tick_status()
        self.refresh_log()
        self.root.after(1000, self.tick)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-season":
        report = Path(sys.argv[2])
        try:
            game_path = find_game()
            game = GameData(install=game_path)
            rows, _ = game.rows()
            variants, images = load_season_art(game, game_path)
            result = {"recipes": len(game.recipes), "owned": sum(row[3] for row in rows),
                      "variants": len(variants), "images": len(images)}
        except Exception as error:
            report.write_text(str(error), encoding="utf-8")
            raise SystemExit(1)
        report.write_text(json.dumps(result), encoding="utf-8")
        raise SystemExit(0)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PCFish.Automation.Pink")
    root = tk.Tk()
    if len(sys.argv) == 3 and sys.argv[1] == "--install-module":
        root.withdraw()
        try:
            install_module(Path(sys.argv[2]))
            messagebox.showinfo("모듈 설치", "설치를 마쳤습니다. 게임을 실행하세요.")
        except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
            messagebox.showerror("모듈 설치 실패", str(error))
        root.destroy()
    else:
        Controller(root)
        root.mainloop()
