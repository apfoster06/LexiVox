"""Windows identity for the app: DPI awareness, app ID, icon.

Without an app ID, Windows labels our notifications and taskbar button "Python".
We set an explicit AppUserModelID and register its display name and icon under
HKCU\\Software\\Classes\\AppUserModelId (per user; no admin rights needed).
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

from .config import APP_NAME

log = logging.getLogger(__name__)

APP_ID = "LexiVox.App"


def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness. Must run before any window (Tk, WebView2, tray) exists,
    otherwise parts of the app use scaled coordinates and others physical ones."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def write_icon(folder: Path) -> tuple[Path, Path]:
    """Save the app icon as .ico (window/taskbar) and .png (notifications)."""
    from .tray import make_icon

    folder.mkdir(parents=True, exist_ok=True)
    ico, png = folder / "app.ico", folder / "app.png"
    img = make_icon("idle", size=256)
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(png)
    return ico, png


def register_app_id(icon_png: Path) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        log.warning("could not set the app ID")
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_png))
    except OSError:
        log.warning("could not register the app name for notifications")


LEGACY_NAME, LEGACY_APP_ID = "Crowe Vibe Whisper", "CroweVibeWhisper.App"


def _programs_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def start_menu_shortcut() -> Path:
    return _programs_dir() / f"{APP_NAME}.lnk"


def remove_legacy_identity() -> None:
    """Remove the shortcut and app-ID entry this app created under its old name."""
    if os.name != "nt":
        return
    try:
        (_programs_dir() / f"{LEGACY_NAME}.lnk").unlink(missing_ok=True)
    except OSError:
        pass
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{LEGACY_APP_ID}")
    except OSError:
        pass  # already gone


def ensure_shortcut(project_root: Path) -> None:
    """Windows shows our name and icon on notifications only when a Start menu shortcut
    carries the app ID, so create one (once) with scripts/make-shortcut.ps1."""
    if os.name != "nt" or start_menu_shortcut().exists():
        return
    import subprocess

    script = project_root / "scripts" / "make-shortcut.ps1"
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, timeout=60, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info("created the Start menu shortcut")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not create the Start menu shortcut: %s", e)


def set_window_icon(title: str, icon_ico: Path) -> None:
    """Give our top-level window (found by title, in this process) the app icon."""
    if os.name != "nt":
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    pid = os.getpid()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if owner.value == pid and buf.value == title:
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x80
    for hwnd in found:
        for which, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
            scale = user32.GetDpiForWindow(hwnd) / 96 if hasattr(user32, "GetDpiForWindow") else 1
            px = int(size * scale)
            handle = user32.LoadImageW(None, str(icon_ico), IMAGE_ICON, px, px, LR_LOADFROMFILE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
