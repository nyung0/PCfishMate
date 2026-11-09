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
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4


BRIDGE = Path(os.environ["LOCALAPPDATA"]) / "PCFishHelper" / "internal"
RARITIES = ("기본", "일반", "고급", "희귀", "전설", "신화")
DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\PC FISH")
ASSETS = Path(__file__).resolve().parent
LOADER_FILES = ("BepInEx", "dotnet", ".doorstop_version", "doorstop_config.ini", "winhttp.dll")


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
        root.title("PC FISH · 내부 자동 교배")
        root.geometry("760x520")
        root.minsize(670, 410)
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="게임 내부 자동 교배", font=("Malgun Gothic", 15, "bold")).pack(anchor="w")
        install = ttk.Frame(panel)
        install.pack(fill="x", pady=(10, 0))
        self.game_path = tk.StringVar(value=str(DEFAULT_GAME))
        ttk.Entry(install, textvariable=self.game_path).pack(side="left", fill="x", expand=True)
        ttk.Button(install, text="폴더 선택", command=self.pick_game).pack(side="left", padx=5)
        ttk.Button(install, text="모듈 설치", command=self.install).pack(side="left")
        ttk.Button(install, text="게임 실행",
                   command=lambda: os.startfile("steam://rungameid/4842670")).pack(side="left", padx=(5, 0))
        self.module_status = tk.StringVar()
        ttk.Label(panel, textvariable=self.module_status).pack(anchor="w", pady=(6, 0))
        self.status = tk.StringVar(value="게임 플러그인 연결 대기 중")
        ttk.Label(panel, textvariable=self.status, wraplength=430).pack(anchor="w", pady=8)
        grades = ttk.Frame(panel)
        grades.pack(anchor="w")
        self.flags = [tk.BooleanVar(value=True) for _ in RARITIES]
        for grade, flag in zip(RARITIES, self.flags):
            ttk.Checkbutton(grades, text=grade, variable=flag).pack(side="left", padx=(0, 8))
        controls = ttk.Frame(panel)
        controls.pack(anchor="w", pady=10)
        self.interval = tk.StringVar(value="10")
        ttk.Label(controls, text="교배 간격 (초)").pack(side="left")
        ttk.Spinbox(controls, textvariable=self.interval, width=5, from_=1, to=3600).pack(side="left", padx=6)
        self.start_button = ttk.Button(controls, text="자동화 시작", command=self.start)
        self.start_button.pack(side="left", padx=4)
        ttk.Button(controls, text="중지", command=self.stop).pack(side="left")
        ttk.Label(panel, text="자동화 로그").pack(anchor="w", pady=(6, 4))
        logs = ttk.Frame(panel)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, height=12, wrap="word", state="disabled",
                           background="#0d2a46", foreground="#e7f7ff", relief="flat")
        scrollbar = ttk.Scrollbar(logs, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.last_log = ""
        self.tick()

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
            text = ""
        if text != self.last_log:
            self.last_log = text
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")

    def write(self, command):
        BRIDGE.mkdir(parents=True, exist_ok=True)
        temporary = BRIDGE / f"{self.run_id}.tmp"
        temporary.write_text(json.dumps(command), encoding="utf-8")
        temporary.replace(BRIDGE / "command.json")

    def start(self):
        selected = [i for i, flag in enumerate(self.flags) if flag.get()]
        try:
            interval = int(self.interval.get())
            if not 1 <= interval <= 3600 or not selected:
                raise ValueError
        except ValueError:
            self.status.set("희귀도를 체크하고 1~3600초 사이의 간격을 입력하세요.")
            return
        self.run_id = uuid4().hex
        self.command = {"RunId": self.run_id, "Enabled": True, "Interval": interval, "Rarities": selected}
        self.tick_status()

    def stop(self):
        self.command = None
        try:
            self.write({"RunId": self.run_id, "Enabled": False, "Interval": 10, "Rarities": []})
        except OSError as error:
            self.status.set(f"중지 요청 전달 실패 · {error}")

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
