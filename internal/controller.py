import json
import os
import ctypes
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import zipfile
import winreg
import stat
import webbrowser
from collections import Counter
from urllib.parse import urlsplit
from queue import Empty, Queue
from threading import Thread
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4
from datetime import datetime
from hashlib import file_digest
from ctypes import wintypes
from urllib.request import urlopen
from PIL import ImageTk
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_tab import CatalogTab, catalog_art
from favorite_notifications import APP_ID, DEFAULT_NOTIFICATION_SETTINGS, flash_taskbar, notify_favorites
from ocean_ui import OCEAN, PANEL, RARITIES, RARITY_COLORS, TEXT, WATER, ocean_style
from season_tab import GameData, SeasonTab, load_automation_icons, load_grade_icon, load_install_fish, load_season_art


BRIDGE = Path(os.environ["LOCALAPPDATA"]) / "PCFishHelper" / "internal"
APP_NAME = "PCfishMate"
RELEASE_PAGE = "https://github.com/nyung0/PCfishMate/releases/latest"
RELEASE_API = "https://api.github.com/repos/nyung0/PCfishMate/releases/latest"
STEAM_TERMS = "https://store.steampowered.com/subscriber_agreement/#4"
INSTALL_NOTICE_VERSION = 1
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


UNITY_LIBRARIES = "https://unity.bepinex.dev/libraries/"


def unity_cache_archive(game):
    bepinex = game / "BepInEx"
    config = bepinex / "config" / "BepInEx.cfg"
    directory = bepinex / "unity-libs"
    for path in (bepinex, config.parent, config, directory):
        if path.exists() and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return None
    if config.exists():
        section = ""
        for line in config.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            if section != "IL2CPP":
                continue
            key, _, value = line.partition("=")
            defaults = {"UnityBaseLibrariesSource": UNITY_LIBRARIES + "{VERSION}.zip",
                        "IL2CPPInteropAssembliesPath": "{BepInEx}"}
            if key.strip() in defaults and value.strip() != defaults[key.strip()]:
                return None

    version = ctypes.windll.version
    dll = str(game / "UnityPlayer.dll")
    size = version.GetFileVersionInfoSizeW(dll, None)
    info = ctypes.create_string_buffer(size)
    data, length = ctypes.c_void_p(), wintypes.UINT()
    if not size or not version.GetFileVersionInfoW(dll, 0, size, info) or not version.VerQueryValueW(
            info, "\\", ctypes.byref(data), ctypes.byref(length)) or length.value < 16:
        raise ValueError("Unity 버전을 확인할 수 없습니다.")
    _, _, high, low = (wintypes.DWORD * 4).from_address(data.value)
    archive = directory / f"{high >> 16}.{high & 0xffff}.{low >> 16}.zip"
    if archive.exists() and archive.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    return archive


def valid_unity_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return ("UnityEngine.CoreModule.dll" in archive.namelist() and archive.testzip() is None)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return False


def repair_unity_cache(game):
    archive = unity_cache_archive(game)
    if archive is None or valid_unity_archive(archive):
        return
    archive.parent.mkdir(exist_ok=True)
    staged = archive.with_name(f"{archive.name}.{uuid4().hex}.tmp")
    bundled = installer_asset(archive.name)
    try:
        if bundled.is_file():
            shutil.copyfile(bundled, staged)
        else:
            with urlopen(UNITY_LIBRARIES + archive.name, timeout=20) as response, staged.open("xb") as output:
                shutil.copyfileobj(response, output)
        if not valid_unity_archive(staged):
            raise ValueError("Unity 라이브러리 ZIP 검증에 실패했습니다. 잠시 후 다시 설치하세요.")
        if archive.exists():
            shutil.copy2(archive, archive.with_name(f"{archive.name}.{uuid4().hex}.bak"))
        staged.replace(archive)
    finally:
        staged.unlink(missing_ok=True)


def installer_asset(name):
    if not getattr(sys, "frozen", False):
        build = ASSETS.parent / "build"
        local = build / "plugin" / name if name == "PCFishInternal.dll" else build / name
        if local.is_file():
            return local
    return ASSETS / name


def install_module(game, report=lambda step: None):
    if not (game / "PCFish.exe").is_file() or not (game / "GameAssembly.dll").is_file():
        raise ValueError("PCFish.exe가 있는 게임 폴더를 선택하세요.")
    processes = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq PCFish.exe", "/NH"],
                                        creationflags=subprocess.CREATE_NO_WINDOW)
    if b"pcfish.exe" in processes.lower():
        raise ValueError("게임을 종료한 뒤 모듈을 설치하세요.")
    present = [name for name in LOADER_FILES if (game / name).exists()]
    if present and len(present) != len(LOADER_FILES):
        raise ValueError("기존 로더 파일 일부가 남아 있어 설치를 중지했습니다.")
    report(20)
    if not present:
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(installer_asset("loader.zip")) as archive:
                archive.extractall(folder)
            for name in LOADER_FILES:
                source = Path(folder) / name
                destination = game / name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
    report(50)
    repair_unity_cache(game)
    report(70)
    if present and ready_game(game):
        return
    plugins = game / "BepInEx" / "plugins"
    plugins.mkdir(exist_ok=True)
    target = plugins / "PCFishInternal.dll"
    staged = plugins / f"PCFishInternal-{uuid4().hex}.tmp"
    try:
        shutil.copy2(installer_asset(target.name), staged)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    report(90)
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


def ready_game(game):
    try:
        if not all((game / name).exists() for name in (*LOADER_FILES, "PCFish.exe", "GameAssembly.dll")):
            return False
        with installer_asset("PCFishInternal.dll").open("rb") as source, (
                game / "BepInEx/plugins/PCFishInternal.dll").open("rb") as installed:
            if file_digest(source, "sha256").digest() != file_digest(installed, "sha256").digest():
                return False
        archive = unity_cache_archive(game)
        return archive is None or valid_unity_archive(archive)
    except (OSError, ValueError):
        return False


def season_merge_exclusions(game):
    assets = (game / "PCFish_Data/resources.assets").read_bytes()
    tables = re.findall(rb"recipeFishGrade,recipeAmount\r?\n((?:[0-9][^\x00\r\n]+\r?\n?)+)", assets)
    rows = [row for table in tables for row in re.findall(
        rb"(?m)^\d+,(FS\d{5}),\d+,(FS\d{5}),(\d+),\d+", table)]
    if not rows:
        raise ValueError("게임에서 시즌 합성 재료 목록을 확인할 수 없습니다.")
    return sorted({target.decode() for target, _, _ in rows}), sorted({
        f"{material.decode()}:{int(grade)}" for _, material, grade in rows})


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("mask", wintypes.ULONG), ("window", wintypes.HWND),
        ("verb", wintypes.LPCWSTR), ("file", wintypes.LPCWSTR),
        ("params", wintypes.LPCWSTR), ("directory", wintypes.LPCWSTR),
        ("show", ctypes.c_int), ("instance", wintypes.HINSTANCE), ("id_list", ctypes.c_void_p),
        ("class_name", wintypes.LPCWSTR), ("class_key", wintypes.HKEY),
        ("hotkey", wintypes.DWORD), ("icon", wintypes.HANDLE), ("process", wintypes.HANDLE),
    ]


def elevated_install(game, progress_address):
    args = ["--install-module", str(game)]
    args.extend((str(progress_address[0]), progress_address[1]))
    if not getattr(sys, "frozen", False):
        args.insert(0, str(Path(__file__).resolve()))
    info = ShellExecuteInfo(size=ctypes.sizeof(ShellExecuteInfo), mask=0x40,
                            verb="runas", file=sys.executable,
                            params=subprocess.list2cmdline(args), show=1)
    launch = ctypes.windll.shell32.ShellExecuteExW
    launch.argtypes = (ctypes.POINTER(ShellExecuteInfo),)
    launch.restype = wintypes.BOOL
    reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    if getattr(sys, "frozen", False):
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        launched = launch(ctypes.byref(info))
    finally:
        if reset is None:
            os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        else:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = reset
    if not launched or not info.process:
        raise OSError("관리자 권한 설치가 시작되지 않았습니다.")
    return info.process


def setup_game(root):
    settings = BRIDGE / "setup.json"
    accepted = False
    try:
        saved = json.loads(settings.read_text(encoding="utf-8"))
        previous = Path(saved["Game"])
        accepted = saved.get("InstallNoticeVersion") == INSTALL_NOTICE_VERSION
    except (OSError, ValueError, KeyError, TypeError):
        previous = None
    if accepted and previous is not None and ready_game(previous):
        return previous

    wizard = tk.Toplevel(root)
    wizard.title(f"{APP_NAME} 설치 마법사")
    wizard.resizable(False, False)
    if (ASSETS / "pcfish.ico").is_file():
        wizard.iconbitmap(str(ASSETS / "pcfish.ico"))
    header = ttk.Frame(wizard, padding=(24, 18, 24, 14))
    header.pack(fill="x")
    heading = tk.StringVar()
    subtitle = tk.StringVar()
    ttk.Label(header, textvariable=heading, font=("Malgun Gothic", 16, "bold")).pack(anchor="w")
    ttk.Label(header, textvariable=subtitle).pack(anchor="w", pady=(4, 0))
    ttk.Separator(wizard).pack(fill="x")
    content = ttk.Frame(wizard, padding=(24, 16))
    content.pack(fill="both", expand=True)
    terms = ttk.Frame(content)
    panel = ttk.Frame(content)
    terms.grid(row=0, column=0, sticky="nsew")
    panel.grid(row=0, column=0, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.rowconfigure(0, weight=1)
    ttk.Label(panel, text="PC FISH 설치 폴더", font=("Malgun Gothic", 10, "bold")).pack(anchor="w")
    path = tk.StringVar(value=str(previous or find_game()))
    row = ttk.Frame(panel)
    row.pack(fill="x", pady=10)
    path_entry = ttk.Entry(row, width=53, textvariable=path)
    path_entry.pack(side="left", fill="x", expand=True)

    def pick():
        chosen = filedialog.askdirectory(parent=wizard, initialdir=path.get())
        if chosen:
            path.set(chosen)

    browse = ttk.Button(row, text="찾아보기…", command=pick)
    browse.pack(side="left", padx=(8, 0))
    ttk.Label(panel, text="PC FISH를 종료한 뒤 설치를 진행하세요. Windows에서 관리자 권한을 요청할 수 있어요.",
              wraplength=510, justify="left").pack(anchor="w", pady=(2, 10))
    notices = (
        ("Steam 이용 약관", "PCfishMate는 비공식 도구입니다. Steam 약관 제4조는 진행이나 보상을 얻는 자동화를 금지하며, 위반 시 계정이나 구독이 제한될 수 있습니다. 설치 동의는 게임 폴더 변경에 적용됩니다."),
        ("게임 폴더 변경", "선택한 게임 폴더에 BepInEx와 모듈을 설치하거나 갱신합니다. 필요한 경우 로더 설정과 Unity 캐시를 변경하고 라이브러리를 다운로드합니다."),
        ("자동화와 데이터", "교배·수조 레벨업·합성을 자동으로 실행할 수 있고, 합성은 재료 물고기를 소비합니다. 도감은 게임 파일과 저장 데이터를 읽으며, 즐겨찾기·설정·기록은 이 PC에 보관합니다."),
        ("알림과 업데이트", "Windows 알림을 켜면 시작 메뉴 바로가기와 알림 발신자가 등록될 수 있습니다. 업데이트 확인은 GitHub에 접속합니다."),
    )
    for title, notice in notices:
        ttk.Label(terms, text=title, font=("Malgun Gothic", 10, "bold")).pack(anchor="w")
        ttk.Label(terms, text=notice, wraplength=510, justify="left").pack(anchor="w", pady=(2, 8))
    ttk.Button(terms, text="Steam 이용 약관 제4조 보기",
               command=lambda: webbrowser.open(STEAM_TERMS)).pack(anchor="w", pady=(0, 8))
    consent = tk.BooleanVar()
    ttk.Checkbutton(terms, text="안내를 확인했고 모듈 설치·갱신에 동의합니다.",
                    variable=consent).pack(anchor="w")
    status = tk.StringVar()
    ttk.Label(panel, textvariable=status, wraplength=475).pack(anchor="w", pady=8)
    progress = tk.Canvas(panel, height=30, highlightthickness=0, bg=panel.winfo_toplevel().cget("bg"))
    progress_value = 0
    progress_fish = None
    progress_socket = None
    progress_token = ""
    stages = {
        0: "게임 폴더 확인 중…",
        20: "BepInEx 로더 준비 중…",
        50: "Unity 라이브러리 확인 중…",
        70: "게임 모듈 적용 중…",
        90: "설치 설정 마무리 중…",
        100: "설치 완료",
    }

    def draw_progress(_event=None):
        progress.delete("all")
        width = max(progress.winfo_width(), 40)
        x = 17 + (width - 34) * progress_value / 100
        progress.create_rectangle(2, 6, width - 2, 24, fill="#CDE7F2", outline="#96B8CC")
        progress.create_rectangle(3, 7, x, 23, fill="#2786AE", width=0)
        progress.create_line(3, 9, x, 9, fill="#64C3E0", width=2)
        if progress_fish is not None:
            progress.create_image(x, 15, image=progress_fish)

    def set_progress(value):
        nonlocal progress_value
        progress_value = max(progress_value, value)
        status.set(f"{stages[progress_value]}  {progress_value}%")
        draw_progress()

    progress.bind("<Configure>", draw_progress)
    ttk.Separator(wizard).pack(fill="x")
    footer = ttk.Frame(wizard, padding=(24, 10))
    footer.pack(fill="x")
    step = tk.StringVar()
    ttk.Label(footer, textvariable=step).pack(side="left")
    buttons = ttk.Frame(footer)
    buttons.pack(side="right")
    result = None
    handle = None

    def cancel():
        if handle is None:
            wizard.destroy()

    def finish(game):
        nonlocal result
        try:
            settings.parent.mkdir(parents=True, exist_ok=True)
            temporary = settings.with_name(f"setup-{uuid4().hex}.tmp")
            temporary.write_text(json.dumps({
                "Game": str(game), "InstallNoticeVersion": INSTALL_NOTICE_VERSION,
            }), encoding="utf-8")
            temporary.replace(settings)
        except OSError as error:
            status.set(f"설치 설정 저장 실패 · {error}")
            return
        result = game
        wizard.destroy()

    def poll(game):
        nonlocal handle, progress_socket
        while True:
            try:
                packet = progress_socket.recv(128).decode("ascii", errors="replace")
            except BlockingIOError:
                break
            token, _, step = packet.partition(":")
            if token == progress_token and step in ("20", "50", "70", "90"):
                set_progress(int(step))
        kernel = ctypes.windll.kernel32
        wait = kernel.WaitForSingleObject
        wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait.restype = wintypes.DWORD
        code = wait(handle, 0)
        if code == 258:
            wizard.after(250, poll, game)
            return
        exit_code = wintypes.DWORD(1)
        get_exit = kernel.GetExitCodeProcess
        get_exit.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        success = code == 0 and get_exit(handle, ctypes.byref(exit_code)) and exit_code.value == 0
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        handle = None
        progress_socket.close()
        progress_socket = None
        if success and ready_game(game):
            set_progress(100)
            wizard.after(350, finish, game)
        else:
            progress.pack_forget()
            set_installing(False)
            status.set("설치를 완료하지 못했습니다. 게임을 종료하고 다시 시도하세요.")

    def set_installing(active):
        for control in (install, back, cancel_button, browse, path_entry):
            control.state(["disabled" if active else "!disabled"])
        heading.set("모듈 설치 중" if active else "설치 위치 선택")
        subtitle.set("설치가 완료되면 PCfishMate가 열려요." if active else "PC FISH가 설치된 Steam 폴더를 선택하세요.")

    def begin():
        nonlocal handle, progress_socket, progress_token, progress_value, progress_fish
        if not consent.get():
            status.set("설치 안내를 읽고 확인란에 체크해 주세요.")
            return
        game = Path(path.get().strip()).resolve()
        if not all((game / name).is_file() for name in ("PCFish.exe", "GameAssembly.dll")):
            status.set("PCFish.exe가 있는 Steam 게임 폴더를 선택하세요.")
            return
        if ready_game(game):
            finish(game)
            return
        progress_fish = None
        try:
            image = load_install_fish(game)
            if image is not None:
                progress_fish = ImageTk.PhotoImage(image, master=wizard)
        except Exception:
            pass  # Installation can still show progress when the game artwork is unavailable.
        try:
            progress_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            progress_socket.bind(("127.0.0.1", 0))
            progress_socket.setblocking(False)
            progress_token = uuid4().hex
            handle = elevated_install(game, (progress_socket.getsockname()[1], progress_token))
        except OSError as error:
            if progress_socket is not None:
                progress_socket.close()
                progress_socket = None
            status.set(str(error))
            return
        set_installing(True)
        progress_value = 0
        progress.pack(fill="x", pady=(0, 10))
        set_progress(0)
        wizard.after(250, poll, game)

    def show_terms():
        panel.grid_remove()
        terms.grid()
        heading.set("PCfishMate 설치 안내")
        subtitle.set("설치 전에 아래 내용을 확인해 주세요.")
        step.set("1 / 2")
        back.state(["disabled"])
        install.configure(text="다음 >", command=show_location)
        update_next()

    def show_location():
        if consent.get():
            terms.grid_remove()
            panel.grid()
            step.set("2 / 2")
            set_installing(False)
            install.configure(text="설치 및 실행", command=begin)
            status.set("")

    def update_next(*_):
        if step.get() == "1 / 2":
            install.state(["!disabled" if consent.get() else "disabled"])

    back = ttk.Button(buttons, text="< 이전", command=show_terms)
    back.pack(side="left", padx=(0, 6))
    install = ttk.Button(buttons, width=14)
    install.pack(side="left", padx=(0, 6))
    cancel_button = ttk.Button(buttons, text="취소", command=cancel)
    cancel_button.pack(side="left")
    consent.trace_add("write", update_next)
    show_terms()
    wizard.update_idletasks()
    content.rowconfigure(0, minsize=terms.winfo_reqheight())
    content.columnconfigure(0, minsize=terms.winfo_reqwidth())
    wizard.protocol("WM_DELETE_WINDOW", cancel)
    root.wait_window(wizard)
    return result


class Controller:
    def __init__(self, root, game):
        self.root = root
        self.update_result = Queue()
        self.download_result = Queue()
        self.command = None
        self.connection_deadline = None
        self.connected = False
        self.incompatible = False
        self.run_id = uuid4().hex
        self.notification_settings_path = BRIDGE / "notification-settings.json"
        self.notification_settings = DEFAULT_NOTIFICATION_SETTINGS.copy()
        try:
            saved = json.loads(self.notification_settings_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.notification_settings.update({
                    key: saved[key] for key in self.notification_settings if isinstance(saved.get(key), bool)
                })
        except (OSError, ValueError):
            pass
        self.settings_window = None
        root.title(APP_NAME)
        icon = ASSETS / "pcfish.ico"
        if icon.is_file():
            root.iconbitmap(default=str(icon))
            root.bind("<Map>", lambda event: root.after_idle(
                lambda: set_taskbar_icon(root, icon)) if event.widget is root else None, add="+")
        root.geometry("820x650")
        root.minsize(670, 410)
        root.protocol("WM_DELETE_WINDOW", self.close)
        ocean_style(root)
        title_bar = tk.Frame(root, bg=WATER)
        title_bar.pack(fill="x")
        header = tk.Label(title_bar, text=APP_NAME, bg=WATER, fg=TEXT,
                          font=("Malgun Gothic", 17, "bold"), anchor="w", padx=20, pady=12)
        badge = ASSETS / "pcfish-badge.png"
        if badge.is_file():
            self.header_icon = tk.PhotoImage(master=root, file=str(badge))
            header.configure(image=self.header_icon, compound="left", padx=14)
        header.pack(side="left")
        ttk.Button(title_bar, text="게임 실행",
                   command=lambda: os.startfile("steam://rungameid/4842670")).pack(side="right", padx=16)
        self.update_button = ttk.Button(title_bar, text="업데이트 확인", command=self.check_update)
        self.update_button.pack(side="right")
        ttk.Button(title_bar, text="알림 설정", command=self.open_settings).pack(side="right", padx=(0, 8))
        self.game_path = tk.StringVar(value=str(game))
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        panel = ttk.Frame(tabs, padding=16)
        tabs.add(panel, text="자동화")
        self.catalog_tab = catalog = CatalogTab(tabs, self.game_path, BRIDGE / "favorites.json")
        season = SeasonTab(tabs, self.game_path, catalog)
        tabs.add(season, text="시즌합성")
        tabs.add(catalog, text="도감")

        def refresh_tab(_event):
            active = tabs.select()
            for tab in (season, catalog):
                if active == str(tab):
                    tab.after_idle(tab.refresh)

        tabs.bind("<<NotebookTabChanged>>", refresh_tab)
        self.status = tk.StringVar(value="게임 플러그인 연결 대기 중")
        status_panel = tk.Frame(panel, bg=PANEL)
        status_panel.pack(fill="x", pady=(0, 10))
        tk.Label(status_panel, textvariable=self.status, bg=PANEL, fg=TEXT, anchor="w",
                 font=("Malgun Gothic", 11, "bold"), padx=12, pady=8,
                 wraplength=560).pack(fill="x")
        self.heart_row = tk.Frame(status_panel, bg=PANEL)
        self.heart_icons = tk.Frame(self.heart_row, bg=PANEL)
        self.heart_icons.pack(side="left", padx=(0, 8))
        self.heart_text = tk.StringVar()
        tk.Label(self.heart_row, textvariable=self.heart_text, bg=PANEL, fg=TEXT,
                 font=("Malgun Gothic", 9)).pack(side="left")
        self.automation_images = {}
        self.heart_display = None
        try:
            self.automation_images = {name: tk.PhotoImage(master=root, data=png)
                                      for name, png in load_automation_icons(game).items()}
        except Exception:
            pass  # Use text when the installed game does not expose these sprites.
        self.heart_images = {
            filled: self.automation_images.get(name)
            for filled, name in ((True, "Icon_BreedCountOn"), (False, "Icon_BreedCountOff"))
        }
        modes = ttk.Frame(panel)
        modes.pack(anchor="w", pady=(0, 6))

        def divider(major=False, parent=panel):
            line = tk.Frame(parent, bg=WATER if major else PANEL,
                            height=2 if major else 1, width=1 if major else 265)
            line.pack(anchor="w", fill="x" if major else "none", pady=(2, 4))
        self.breed_enabled = tk.BooleanVar(value=True)
        self.upgrade_enabled = tk.BooleanVar(value=True)
        self.merge_enabled = tk.BooleanVar(value=False)
        self.same_breed = tk.BooleanVar(value=False)
        self.same_breed_stars = tk.BooleanVar(value=False)
        self.breed_star_flags = [tk.BooleanVar(value=True) for _ in range(6)]
        self.same_merge = tk.BooleanVar(value=True)
        self.same_merge_stars = tk.BooleanVar(value=True)
        self.merge_filters = [tk.BooleanVar(value=True) for _ in range(4)]
        self.merge_rarities = [tk.BooleanVar(value=True) for _ in range(5)]
        self.merge_exclusions = None
        for label, flag, sprite in (("교배 자동화", self.breed_enabled, "Icon_BreedHeart"),
                                    ("수조 레벨업 자동화", self.upgrade_enabled, "Icon_LvUp"),
                                    ("합성 자동화", self.merge_enabled, "Icon_Merge")):
            ttk.Checkbutton(modes, text=label, variable=flag,
                            image=self.automation_images.get(sprite) or "", compound="left",
                            style="Ocean.TCheckbutton", command=self.update_modes).pack(side="left", padx=(0, 14))
        divider(major=True)
        options_row = ttk.Frame(panel)
        options_row.pack(fill="x")
        for column in range(2):
            options_row.columnconfigure(column, weight=1, uniform="options")

        breed_options = ttk.Frame(options_row)
        merge_options = ttk.Frame(options_row)
        sections = (("교배 옵션", breed_options), ("합성 옵션", merge_options))
        option_buttons = []

        def show_options(selected):
            opened = selected is not None and not selected.winfo_manager()
            for (title, body), button in zip(sections, option_buttons):
                active = opened and body is selected
                if active:
                    body.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
                else:
                    body.grid_remove()
                button.configure(text=f"{title}  {'▾' if active else '▸'}")

        for column, (title, body) in enumerate(sections):
            button = ttk.Button(options_row, text=title, command=lambda section=body: show_options(section))
            button.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else (6, 0))
            option_buttons.append(button)
        show_options(breed_options)
        self.build_merge_options(merge_options, divider)
        ttk.Label(breed_options, text="교배 허용 희귀도 · 두 부모 모두 적용").pack(anchor="w", pady=(2, 4))
        grades = ttk.Frame(breed_options)
        grades.pack(anchor="w")
        self.flags = [tk.BooleanVar(value=True) for _ in RARITIES]
        style = ttk.Style(root)
        for index, (grade, flag) in enumerate(zip(RARITIES, self.flags)):
            rarity_style = f"Rarity{index}.Ocean.TCheckbutton"
            style.configure(rarity_style, foreground=RARITY_COLORS[index])
            style.map(rarity_style, foreground=[("active", RARITY_COLORS[index])])
            ttk.Checkbutton(grades, text=grade, variable=flag,
                            style=rarity_style, command=self.update_modes).pack(side="left", padx=(0, 8))
        divider(parent=breed_options)
        ttk.Label(breed_options, text="교배 허용 별 등급 · 두 부모 모두 적용").pack(anchor="w", pady=(5, 2))
        stars = ttk.Frame(breed_options)
        stars.pack(anchor="w")
        try:
            icon = load_grade_icon(game)
        except Exception:
            icon = None
        self.star_images = []
        if icon:
            sprite = tk.PhotoImage(master=root, data=icon)
            for count in range(1, 6):
                width, height = sprite.width(), sprite.height()
                strip = tk.PhotoImage(master=root, width=count * (width + 1) - 1, height=height)
                for index in range(count):
                    strip.tk.call(strip, "copy", sprite, "-to", index * (width + 1), 0)
                self.star_images.append(strip)
        for count, flag in enumerate(self.breed_star_flags):
            label = "별 없음" if count == 0 else "★" * count
            appearance = {"image": self.star_images[count - 1]} if count and self.star_images else {}
            ttk.Checkbutton(stars, text=label, variable=flag, style="Ocean.TCheckbutton", **appearance,
                            command=self.update_modes).pack(side="left", padx=(0, 8))
        divider(parent=breed_options)
        matching = ttk.Frame(breed_options)
        matching.pack(anchor="w", pady=(5, 0))
        for label, flag in (("같은 희귀도끼리만 교배", self.same_breed),
                            ("같은 별 등급끼리만 교배", self.same_breed_stars)):
            ttk.Checkbutton(matching, text=label, variable=flag, style="Ocean.TCheckbutton",
                            command=self.update_modes).pack(side="left", padx=(0, 12))
        divider(parent=breed_options)
        priority = ttk.Frame(breed_options)
        priority.pack(anchor="w", pady=(2, 0))
        ttk.Label(priority, text="교배 우선순위").pack(side="left", padx=(0, 8))
        self.priority = ttk.Combobox(priority, state="readonly", width=34,
                                    values=("희귀도 높은 순 → 별 등급 높은 순", "별 등급 높은 순 → 희귀도 높은 순"))
        self.priority.current(0)
        self.priority.bind("<<ComboboxSelected>>", lambda _: self.update_modes())
        self.priority.pack(side="left")
        divider(major=True)
        controls = ttk.Frame(panel)
        controls.pack(anchor="w", pady=10)
        self.interval = tk.StringVar(value="10")
        ttk.Label(controls, text="교배·합성 간격 (초)").pack(side="left")
        ttk.Spinbox(controls, textvariable=self.interval, width=5, from_=1, to=3600).pack(side="left", padx=6)
        self.start_button = ttk.Button(controls, text="자동화 시작", command=self.start,
                                       style="Primary.TButton")
        self.start_button.pack(side="left", padx=4)
        ttk.Button(controls, text="중지", command=self.stop, style="Stop.TButton").pack(side="left")
        ttk.Button(controls, text="로그 보기", command=self.open_log).pack(side="left", padx=6)
        divider(major=True)
        ttk.Label(panel, text="자동화 기록").pack(anchor="w", pady=(6, 4))
        history_panel = ttk.Frame(panel)
        history_panel.pack(fill="both", expand=True)
        self.history = ttk.Treeview(history_panel, columns=("time", "kind", "detail"), show="headings")
        for column, label, width in (("time", "시간", 155), ("kind", "유형", 105), ("detail", "내용", 420)):
            self.history.heading(column, text=label)
            self.history.column(column, width=width, anchor="w", stretch=column == "detail")
        scrollbar = ttk.Scrollbar(history_panel, command=self.history.yview)
        self.history.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.history.pack(side="left", fill="both", expand=True)
        self.log_window = None
        self.last_log = ""
        self.last_history = ()
        self.notifications_started = datetime.now().astimezone()
        self.catalog_path = None
        self.catalog = None
        self.tick()

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(self.root)
        window.title(f"{APP_NAME} · 알림 설정")
        window.configure(bg=OCEAN)
        window.transient(self.root)
        window.resizable(False, False)
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="즐겨찾기 물고기 알림", font=("Malgun Gothic", 11, "bold")).pack(anchor="w")
        draft = {}
        for key, label in (("windows", "Windows 팝업 알림"), ("taskbar", "작업표시줄 아이콘 깜박임")):
            draft[key] = tk.BooleanVar(window, value=self.notification_settings[key])
            ttk.Checkbutton(panel, text=label, variable=draft[key], style="Ocean.TCheckbutton").pack(
                anchor="w", pady=(8, 0))
        ttk.Label(
            panel, wraplength=355, justify="left",
            text="방해 금지 중에도 팝업을 받으려면 Windows 알림 설정의 "
                 "‘우선 순위 알림 설정 → 앱 추가’에서 PCfishMate를 선택하세요. "
                 "앱이 목록에 없으면 첫 알림을 받은 뒤 다시 확인하세요.",
        ).pack(anchor="w", pady=(12, 6))

        def open_windows_notifications():
            try:
                os.startfile("ms-settings:notifications")
            except OSError:
                messagebox.showinfo("Windows 알림 설정",
                                    "Windows 설정 → 시스템 → 알림에서 열 수 있어요.", parent=window)

        ttk.Button(panel, text="Windows 알림 설정 열기",
                   command=open_windows_notifications).pack(anchor="w")
        buttons = ttk.Frame(panel)
        buttons.pack(anchor="e", pady=(14, 0))

        def save():
            settings = {key: value.get() for key, value in draft.items()}
            if self.save_notification_settings(settings, window):
                window.destroy()

        ttk.Button(buttons, text="취소", command=window.destroy).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="확인", command=save, style="Primary.TButton").pack(side="left")

    def save_notification_settings(self, settings, parent):
        path = self.notification_settings_path
        temporary = path.with_name(f"notification-settings-{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(settings), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            messagebox.showerror("알림 설정 저장 실패", str(error), parent=parent)
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        if self.notification_settings["taskbar"] and not settings["taskbar"]:
            flash_taskbar(self.root, enabled=False)
        self.notification_settings = settings
        return True

    def build_merge_options(self, panel, divider):
        for heading, labels, flags, columns in (
            ("자동 등록 조건", ("남은 교배 0회만", "수조 배치 제외",
                              "시즌 물고기 제외", "시즌 재료 제외"), self.merge_filters, 4),
            ("종 희귀도 조건", RARITIES[1:], self.merge_rarities, 5),
            ("재료 조합", ("같은 별 등급 10마리만 합성", "같은 희귀도 10마리만 합성"),
             (self.same_merge_stars, self.same_merge), 2),
        ):
            if panel.winfo_children():
                divider(parent=panel)
            ttk.Label(panel, text=heading).pack(anchor="w", pady=(4, 1))
            group = ttk.Frame(panel)
            group.pack(fill="x")
            for index, (label, flag) in enumerate(zip(labels, flags)):
                ttk.Checkbutton(group, text=label, variable=flag, style="Ocean.TCheckbutton",
                                command=self.update_modes).grid(
                    row=index // columns, column=index % columns, sticky="w", padx=(0, 6))

    def mode_settings(self):
        zero, placed, season, materials = (flag.get() for flag in self.merge_filters)
        if self.merge_enabled.get() and (season or materials) and self.merge_exclusions is None:
            self.merge_exclusions = season_merge_exclusions(Path(self.game_path.get()))
        season_ids, material_ids = self.merge_exclusions or ([], [])
        return {
            "Breed": self.breed_enabled.get(), "TankUpgrade": self.upgrade_enabled.get(),
            "AutoMerge": self.merge_enabled.get(), "SameBreedRarity": self.same_breed.get(),
            "SameBreedStars": self.same_breed_stars.get(),
            "BreedStars": [i for i, flag in enumerate(self.breed_star_flags) if flag.get()],
            "StarsFirst": self.priority.current() == 1,
            "Rarities": [i for i, flag in enumerate(self.flags) if flag.get()],
            "Merge": {"SameRarity": self.same_merge.get(), "SameStars": self.same_merge_stars.get(),
                      "ZeroBreeds": zero,
                      "ExcludePlaced": placed, "ExcludeSeason": season, "ExcludeSeasonMaterials": materials,
                      "Rarities": [i for i, flag in enumerate(self.merge_rarities, 1) if flag.get()],
                      "SeasonIds": season_ids, "SeasonMaterials": material_ids},
        }

    def check_update(self):
        self.update_button.state(["disabled"])

        def fetch():
            try:
                version_file = ASSETS / "release-version.txt"
                current = version_file.read_text(encoding="ascii").strip() if version_file.is_file() else "v0.5.1"
                with urlopen(RELEASE_API, timeout=10) as response:
                    release = json.load(response)
                latest = release["tag_name"]
                versions = []
                for version in (current, latest):
                    if not re.fullmatch(r"v?\d+\.\d+\.\d+", version):
                        raise ValueError("릴리스 버전 형식을 확인할 수 없습니다.")
                    versions.append(tuple(map(int, version.lstrip("v").split("."))))
                asset = next((item for item in release["assets"] if item["name"] == "PCfishMate.exe"), None)
                self.update_result.put((current, latest, versions[1] > versions[0], asset, None))
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.update_result.put((None, None, False, None, str(error)))

        Thread(target=fetch, daemon=True).start()

    def show_update_result(self):
        try:
            current, latest, newer, asset, error = self.update_result.get_nowait()
        except Empty:
            return
        self.update_button.state(["!disabled"])
        if error:
            messagebox.showerror("업데이트 확인", f"GitHub 릴리스를 확인하지 못했습니다.\n{error}", parent=self.root)
        elif newer:
            if messagebox.askyesno("업데이트 확인", f"업데이트 내용이 있습니다. 업데이트 하시겠습니까?\n\n현재 {current} · 최신 {latest}",
                                   parent=self.root):
                if getattr(sys, "frozen", False):
                    self.download_update(latest, asset)
                else:
                    webbrowser.open(RELEASE_PAGE)
        else:
            messagebox.showinfo("업데이트 확인", f"최신 버전입니다.\n\n현재 버전 {current}", parent=self.root)

    def download_update(self, tag, asset):
        self.update_button.state(["disabled"])
        self.status.set("업데이트 다운로드 중…")

        def fetch():
            staged = None
            try:
                url = f"https://github.com/nyung0/PCfishMate/releases/download/{tag}/PCfishMate.exe"
                if not asset or asset.get("browser_download_url") != url:
                    raise ValueError("공식 Windows 업데이트 파일을 찾을 수 없습니다.")
                digest = asset.get("digest") or ""
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError("업데이트 파일의 SHA-256을 확인할 수 없습니다.")
                with tempfile.NamedTemporaryFile(prefix="PCfishMate-update-", suffix=".exe", delete=False) as output:
                    staged = Path(output.name)
                    with urlopen(url, timeout=30) as response:
                        if urlsplit(response.url).scheme != "https":
                            raise ValueError("업데이트 다운로드 연결을 확인할 수 없습니다.")
                        shutil.copyfileobj(response, output)
                with staged.open("rb") as stream:
                    if file_digest(stream, "sha256").hexdigest() != digest[7:]:
                        raise ValueError("업데이트 파일 검증에 실패했습니다.")
                self.download_result.put((staged, None))
            except (OSError, ValueError) as error:
                if staged:
                    staged.unlink(missing_ok=True)
                self.download_result.put((None, str(error)))

        Thread(target=fetch, daemon=True).start()

    def finish_update_download(self):
        try:
            staged, error = self.download_result.get_nowait()
        except Empty:
            return
        self.update_button.state(["!disabled"])
        if error:
            messagebox.showerror("업데이트 실패", error, parent=self.root)
            return
        try:
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
            subprocess.Popen([str(staged), "--apply-update", str(os.getpid()), sys.executable], env=env)
        except OSError as error:
            staged.unlink(missing_ok=True)
            messagebox.showerror("업데이트 실패", str(error), parent=self.root)
            return
        self.close()
        return True

    def open_log(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        self.log_window = tk.Toplevel(self.root)
        self.log_window.title(f"{APP_NAME} · 자동화 로그")
        self.log_window.geometry("740x420")
        logs = ttk.Frame(self.log_window, padding=12)
        logs.pack(fill="both", expand=True)
        self.log_module_version = tk.StringVar(master=logs)
        ttk.Label(logs, textvariable=self.log_module_version).pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(logs)
        body.pack(fill="both", expand=True)
        self.log = tk.Text(body, height=12, wrap="word", state="disabled",
                           background=PANEL, foreground=TEXT, relief="flat")
        scrollbar = ttk.Scrollbar(body, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.show_log(self.last_log)
        self.refresh_log_version()

    def refresh_log_version(self):
        if self.log_window is None or not self.log_window.winfo_exists():
            return
        label = "모듈 버전 · 게임 연결 대기 중"
        if self.connected:
            log = Path(self.game_path.get()) / "BepInEx/LogOutput.log"
            try:
                with log.open("rb") as stream:
                    startup = stream.read(131072)
                versions = re.findall(rb"Loading \[PC Fish Internal Breeding ([^\]\r\n]+)\]", startup)
            except OSError:
                versions = []
            label = "모듈 버전 · " + (versions[-1].decode("ascii", errors="replace") if versions else "확인할 수 없음")
        self.log_module_version.set(label)

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

    def refresh_log(self):
        game = Path(self.game_path.get())
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
        event_path = BRIDGE / "events.jsonl"
        try:
            with event_path.open("rb") as stream:
                stream.seek(max(0, event_path.stat().st_size - 96_000))
                records = tuple(stream.read().decode("utf-8", errors="replace").splitlines()[-250:])
        except OSError:
            return
        if records == self.last_history and game == self.catalog_path:
            return
        new_records = Counter(records) - Counter(self.last_history)
        self.last_history = records
        if game != self.catalog_path:
            self.catalog_path, self.catalog = game, None
        if records and self.catalog is None:
            try:
                self.catalog = GameData(install=game)
            except (OSError, ValueError):
                pass
        self.history.delete(*self.history.get_children())
        notifications = []
        for line in reversed(records):
            try:
                event = json.loads(line)
                occurred = datetime.fromisoformat(event["Time"]).astimezone()
                stamp = occurred.strftime("%Y-%m-%d %H:%M:%S")
                kind, detail = event["Kind"], event["Message"]
                child = event.get("Child")
                if (new_records[line] and occurred >= self.notifications_started
                        and kind in ("교배 완료", "합성 완료") and isinstance(child, str)
                        and child in self.catalog_tab.favorites and self.catalog
                        and child in self.catalog.variants):
                    species, _, _ = self.catalog.variants[child]
                    notifications.append((child, self.catalog.names.get(species, species)))
                    new_records[line] -= 1
                if kind == "교배 요청":
                    continue
                if kind == "교배 완료":
                    first, second = map(self.fish_label, event["Parents"])
                    detail = f"{first} + {second} → {self.fish_label(event['Child'])}"
                elif kind == "합성 완료" and "Child" in event:
                    detail = f"{detail} → {self.fish_label(event['Child'])}"
            except (KeyError, TypeError, ValueError):
                continue
            self.history.insert("", "end", values=(stamp, kind, detail))
        if notifications:
            self.send_favorite_notifications(game, list(reversed(notifications)))

    def send_favorite_notifications(self, game, notifications):
        if self.notification_settings["taskbar"]:
            flash_taskbar(self.root)
        if self.notification_settings["windows"]:
            Thread(target=notify_favorites, args=(game, BRIDGE / "notification-art",
                                                  notifications, lambda: self.notification_settings["windows"]),
                   daemon=True).start()

    def write(self, command):
        BRIDGE.mkdir(parents=True, exist_ok=True)
        temporary = BRIDGE / f"{self.run_id}.tmp"
        temporary.write_text(json.dumps(command), encoding="utf-8")
        temporary.replace(BRIDGE / "command.json")

    def start(self):
        try:
            modes = self.mode_settings()
        except (OSError, ValueError) as error:
            self.status.set(f"합성 설정 오류 · {error}")
            return
        if not any(modes[key] for key in ("Breed", "TankUpgrade", "AutoMerge")):
            self.status.set("실행할 자동화 기능을 하나 이상 선택하세요.")
            return
        try:
            interval = int(self.interval.get()) if modes["Breed"] or modes["AutoMerge"] else 10
            if not 1 <= interval <= 3600:
                raise ValueError
        except ValueError:
            self.status.set("1~3600초 사이의 간격을 입력하세요.")
            return
        self.run_id = uuid4().hex
        self.command = {"RunId": self.run_id, "Enabled": True, "Interval": interval, **modes}
        self.tick_status()
        if self.connected:
            return
        if self.incompatible:
            self.command = None
            return
        try:
            os.startfile("steam://rungameid/4842670")
        except OSError as error:
            self.command = None
            self.status.set(f"게임 실행 실패 · {error}")
            return
        self.connection_deadline = time.monotonic() + 90
        self.tick_status()

    def stop(self):
        self.command = None
        self.connection_deadline = None
        self.start_button.state(["!disabled"])
        try:
            self.write({"RunId": self.run_id, "Enabled": False, "Interval": 10, "Rarities": []})
        except OSError as error:
            self.status.set(f"중지 요청 전달 실패 · {error}")

    def update_modes(self):
        if self.command is None:
            return
        try:
            modes = self.mode_settings()
        except (OSError, ValueError) as error:
            self.stop()
            self.status.set(f"합성 설정 오류 · {error}")
            return
        if not any(modes[key] for key in ("Breed", "TankUpgrade", "AutoMerge")):
            self.stop()
            return
        self.command.update(modes)
        self.tick_status()

    def close(self):
        self.stop()
        self.root.destroy()

    def update_heart_status(self, state):
        count = state.get("HeartCount")
        maximum = state.get("HeartMax")
        if type(count) is not int or count < 0:
            self.heart_row.pack_forget()
            return
        known_max = type(maximum) is int and maximum > 0
        display = (count, maximum if known_max else None)
        if display != self.heart_display:
            for icon in self.heart_icons.winfo_children():
                icon.destroy()
            if known_max and maximum <= 10:
                for index in range(maximum):
                    filled = index < count
                    image = self.heart_images.get(filled)
                    tk.Label(self.heart_icons, bg=PANEL, fg=TEXT, image=image or "",
                             text="" if image else ("♥" if filled else "♡")).pack(side="left", padx=1)
            self.heart_display = display
        label = f"교배 하트 {count}/{maximum}" if known_max else f"교배 하트 {count}개"
        next_at = state.get("NextHeartAt")
        if known_max and count >= maximum:
            label += " · 가득 참"
        elif type(next_at) is int and next_at > 0:
            seconds = max(0, next_at - int(time.time()))
            minutes, seconds = divmod(seconds, 60)
            label += f" · 다음 하트까지 {minutes}분 {seconds:02d}초"
        self.heart_text.set(label)
        self.heart_row.pack(anchor="w", padx=12, pady=(0, 7))

    def tick_status(self):
        self.connected = False
        self.incompatible = False
        heart_state = {}
        try:
            path = BRIDGE / "status.json"
            connected = time.time() - path.stat().st_mtime < 5
            if connected:
                state = json.loads(path.read_text(encoding="utf-8"))
                status = ("실행 중 · " if state["Running"] else "") + state["Message"]
                if state.get("MergeMessage"):
                    status += "\n합성 · " + state["MergeMessage"]
                for error in state.get("FeatureErrors", []):
                    status += "\n" + error
                self.status.set(status)
                connected = state.get("Version") == 3
                if not connected:
                    self.incompatible = True
                    self.command = None
                    self.connection_deadline = None
                    self.status.set("새 모듈을 적용하려면 게임을 다시 시작하세요.")
                else:
                    self.connected = True
                    heart_state = state
                    self.connection_deadline = None
                    if self.command:
                        self.write(self.command)
            else:
                self.status.set("게임 플러그인 연결 대기 중")
        except (OSError, ValueError, KeyError):
            self.status.set("게임 플러그인 연결 대기 중")
        self.update_heart_status(heart_state)
        if self.connection_deadline is not None:
            if time.monotonic() < self.connection_deadline:
                self.status.set("게임 시작 중 · 모듈이 연결되면 자동화를 시작합니다.")
            else:
                self.command = None
                self.connection_deadline = None
                self.status.set("게임 모듈 연결 시간 초과 · 게임을 확인하고 다시 시작하세요.")
        self.start_button.state(["disabled"] if self.connection_deadline is not None else ["!disabled"])

    def tick(self):
        self.tick_status()
        self.refresh_log()
        self.refresh_log_version()
        self.show_update_result()
        if self.finish_update_download():
            return
        self.root.after(1000, self.tick)


def apply_update(parent_pid, target):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    parent = kernel.OpenProcess(0x00100000, False, parent_pid)
    if parent:
        try:
            if kernel.WaitForSingleObject(parent, 30000) != 0:
                raise OSError("이전 PCfishMate가 종료되지 않았습니다.")
        finally:
            kernel.CloseHandle(parent)
    elif ctypes.get_last_error() != 87:
        raise ctypes.WinError(ctypes.get_last_error())

    if target.suffix.lower() != ".exe" or not target.is_file():
        raise ValueError("기존 PCfishMate 실행 파일을 찾을 수 없습니다.")
    staged = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    backup = staged.with_suffix(".bak")
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        shutil.copyfile(sys.executable, staged)
        shutil.copyfile(target, backup)
        deadline = time.monotonic() + 10
        while True:
            try:
                staged.replace(target)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        try:
            subprocess.Popen([str(target)], env=env)
        except OSError:
            try:
                backup.replace(target)
            except OSError as error:
                raise OSError(f"이전 버전 백업을 복원하지 못했습니다. 백업 파일: {backup}") from error
            raise
        else:
            backup.unlink(missing_ok=True)
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--check-image-runtime"]:
        from UnityPy.export import SpriteHelper
        raise SystemExit(0 if callable(SpriteHelper.get_image_from_sprite) else 1)
    if getattr(sys, "frozen", False) and len(sys.argv) == 4 and sys.argv[1] == "--apply-update":
        try:
            apply_update(int(sys.argv[2]), Path(sys.argv[3]))
        except (OSError, ValueError) as error:
            ctypes.windll.user32.MessageBoxW(None, str(error), "PCfishMate 업데이트 실패", 0x10)
            raise SystemExit(1)
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--check-season":
        report = Path(sys.argv[2])
        try:
            game_path = find_game()
            game = GameData(install=game_path)
            rows, _ = game.rows()
            variants, images = load_season_art(game, game_path)
            catalog_images = catalog_art(game_path, game.variants)
            if not images or not catalog_images:
                raise ValueError("시즌 또는 도감 이미지를 읽지 못했습니다.")
            result = {"recipes": len(game.recipes), "owned": sum(row[3] for row in rows),
                      "variants": len(variants), "images": len(images),
                      "catalog_images": len(catalog_images)}
        except Exception as error:
            report.write_text(str(error), encoding="utf-8")
            raise SystemExit(1)
        report.write_text(json.dumps(result), encoding="utf-8")
        raise SystemExit(0)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    root = tk.Tk()
    root.withdraw()
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--install-module":
        try:
            if len(sys.argv) == 5:
                port, token = int(sys.argv[3]), sys.argv[4]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reporter:
                    def report(step):
                        try:
                            reporter.sendto(f"{token}:{step}".encode("ascii"), ("127.0.0.1", port))
                        except OSError:
                            pass

                    install_module(Path(sys.argv[2]), report)
            else:
                install_module(Path(sys.argv[2]))
        except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
            messagebox.showerror("모듈 설치 실패", str(error), parent=root)
            raise SystemExit(1)
        finally:
            root.destroy()
    else:
        game = setup_game(root)
        if game is None:
            root.destroy()
        else:
            Controller(root, game)
            root.deiconify()
            root.mainloop()
