import base64
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
import winreg
from xml.etree import ElementTree as ET
from uuid import uuid4

from catalog_tab import catalog_art


APP_ID = "PCFish.Automation.Pink"
DEFAULT_NOTIFICATION_SETTINGS = {"windows": False, "taskbar": False}
SHORTCUT_LOCK = Lock()
SHOW_TOAST = """
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:PCFISHMATE_TOAST_XML)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:PCFISHMATE_TOAST_APP)
if ($notifier.Setting.ToString() -ne 'Enabled') {
    [Console]::Error.WriteLine("Windows notification setting: $($notifier.Setting)")
    exit 3
}
$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""
REGISTER_SHORTCUT = """
$ErrorActionPreference = 'Stop'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'PCfishMate.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $env:PCFISHMATE_EXE
$shortcut.WorkingDirectory = Split-Path -Parent $env:PCFISHMATE_EXE
$shortcut.Save()
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class ToastShortcut {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey { public Guid format; public uint id; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    public struct PropertyValue {
        [FieldOffset(0)] public ushort type;
        [FieldOffset(8)] public IntPtr text;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropertyValue value);
        void SetValue(ref PropertyKey key, ref PropertyValue value);
        void Commit();
    }

    public static void SetAppId(string path, string appId) {
        var clsid = new Guid("00021401-0000-0000-C000-000000000046");
        var link = Activator.CreateInstance(Type.GetTypeFromCLSID(clsid));
        var file = (IPersistFile)link;
        var key = new PropertyKey {
            format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), id = 5
        };
        var value = new PropertyValue { type = 31, text = Marshal.StringToCoTaskMemUni(appId) };
        try {
            file.Load(path, 2);
            var store = (IPropertyStore)link;
            store.SetValue(ref key, ref value);
            store.Commit();
            file.Save(path, true);
        } finally {
            Marshal.FreeCoTaskMem(value.text);
            Marshal.FinalReleaseComObject(link);
        }
    }
}
'@
[ToastShortcut]::SetAppId($shortcutPath, $env:PCFISHMATE_TOAST_APP)
"""


class FlashWindowInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.UINT), ("window", wintypes.HWND), ("flags", wintypes.DWORD),
        ("count", wintypes.UINT), ("timeout", wintypes.DWORD),
    ]


def flash_taskbar(root, enabled=True):
    user32 = ctypes.windll.user32
    get_ancestor = user32.GetAncestor
    get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    get_ancestor.restype = wintypes.HWND
    window = get_ancestor(root.winfo_id(), 2)  # GA_ROOT
    if not window:
        return
    flash = user32.FlashWindowEx
    flash.argtypes = (ctypes.POINTER(FlashWindowInfo),)
    flash.restype = wintypes.BOOL
    # FLASHW_TRAY flashes the taskbar button without activating the window.
    info = FlashWindowInfo(ctypes.sizeof(FlashWindowInfo), window, 2 if enabled else 0,
                           3 if enabled else 0, 0)
    flash(ctypes.byref(info))


def run_notification_script(script, env):
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                   env=env, check=True, capture_output=True, timeout=20,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def register_notification_shortcut(executable):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
        try:
            registered, _ = winreg.QueryValueEx(key, "PCfishMateShortcutTarget")
        except FileNotFoundError:
            registered = None
        shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/PCfishMate.lnk"
        if registered == str(executable) and shortcut.is_file():
            return
    env = os.environ.copy()
    env["PCFISHMATE_EXE"] = str(executable)
    env["PCFISHMATE_TOAST_APP"] = APP_ID
    run_notification_script(REGISTER_SHORTCUT, env)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
        winreg.SetValueEx(key, "PCfishMateShortcutTarget", 0, winreg.REG_SZ, str(executable))


def show_fish_toast(name, picture=None):
    with SHORTCUT_LOCK:
        if getattr(sys, "frozen", False):
            try:
                register_notification_shortcut(Path(sys.executable).resolve())
            except (OSError, KeyError, subprocess.SubprocessError):
                logging.exception("Could not register the PCfishMate notification shortcut")
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "PCfishMate")
        except OSError:
            logging.exception("Could not update the Windows notification display name")

    toast = ET.Element("toast")
    binding = ET.SubElement(ET.SubElement(toast, "visual"), "binding", template="ToastGeneric")
    ET.SubElement(binding, "text").text = f"{name} 나왔다!"
    if picture is not None and picture.is_file():
        ET.SubElement(binding, "image", placement="appLogoOverride", src=picture.resolve().as_uri())
    env = os.environ.copy()
    env["PCFISHMATE_TOAST_XML"] = ET.tostring(toast, encoding="unicode")
    env["PCFISHMATE_TOAST_APP"] = APP_ID
    try:
        run_notification_script(SHOW_TOAST, env)
    except subprocess.CalledProcessError as error:
        image = binding.find("image")
        if image is None or error.returncode == 3:
            raise
        binding.remove(image)
        env["PCFISHMATE_TOAST_XML"] = ET.tostring(toast, encoding="unicode")
        run_notification_script(SHOW_TOAST, env)


def notify_favorites(game, cache, notifications, enabled):
    pictures = {}
    try:
        pictures = catalog_art(game, {variant for variant, _ in notifications})
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:
        logging.exception("Could not prepare favorite fish artwork")
    for variant, name in notifications:
        if not enabled():
            return
        picture = None
        if (variant, True) in pictures:
            temporary = cache / f"{uuid4().hex}.tmp"
            try:
                temporary.write_bytes(pictures[variant, True])
                picture = temporary.replace(cache / f"{variant}.png")
            except OSError:
                logging.exception("Could not cache favorite fish artwork")
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            show_fish_toast(name, picture)
        except (OSError, KeyError, subprocess.SubprocessError) as error:
            logging.exception("Could not show favorite fish notification")
            try:
                details = getattr(error, "stderr", None)
                diagnostic = details.decode("utf-8", errors="replace") if details else str(error)
                (cache.parent / "notification-error.txt").write_text(diagnostic[:4000], encoding="utf-8")
            except OSError:
                pass
