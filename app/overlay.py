"""Small always-on-top "Recording" pill shown while dictating from another app.

It's a plain Win32 window on its own thread, styled so it
can never take focus (so it can't steal the paste), doesn't appear in the taskbar, and
lets clicks pass through. The picture comes from overlay_render.py and is pushed to
the screen as a per-pixel-alpha layered window, so its edges and shadow are smooth.
It's hidden whenever the app's own window is in front, since that shows the state.

Positions: "bottom" = horizontal, centred just above the taskbar; "right" = vertical,
pinned to the right edge; "off". It appears on the monitor you're working on.
The process is per-monitor DPI aware (see winshell.py), so sizes are scaled here.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT = 0x8, 0x80, 0x20
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOACTIVATE = 0x10
HWND_TOPMOST = -1
MONITOR_DEFAULTTOPRIMARY = 1
ULW_ALPHA = 2
WS_POPUP = 0x80000000
PM_REMOVE = 1

STATES = ("recording", "transcribing", "polishing")
LONG, SHORT = 210, 38  # pill size at 100% scaling
MARGIN = 16
FRAME_MS, IDLE_MS = 33, 100  # redraw interval while shown / while hidden
FADE_SECONDS = 0.16


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


def pill_rect(position: str, work: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the pill inside a monitor's work area."""
    left, top, right, bottom = work
    long_, short, margin = round(LONG * scale), round(SHORT * scale), round(MARGIN * scale)
    if position == "right":
        return right - short - margin, top + (bottom - top - long_) // 2, short, long_
    return left + (right - left - long_) // 2, bottom - short - margin, long_, short


class RecordingOverlay:
    def __init__(
        self,
        get_position: Callable[[], str] = lambda: "bottom",
        get_level: Callable[[], float] | None = None,
    ) -> None:
        self._get_position = get_position
        self._get_level = get_level  # live mic peak; smoother than the snapshots' level
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._snap: dict = {"state": "idle"}

    def start(self) -> None:
        if os.name != "nt" or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self._thread.start()

    def update(self, snapshot: dict) -> None:
        self._queue.put(snapshot)

    def stop(self) -> None:
        self._queue.put(None)

    # ---- overlay thread -----------------------------------------------------------

    def _run(self) -> None:
        try:
            from app.overlay_render import PillRenderer
        except ImportError:
            log.warning("Pillow not available; no recording overlay")
            return
        user32 = ctypes.WinDLL("user32")
        gdi32 = ctypes.WinDLL("gdi32")
        handle = ctypes.c_void_p
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, handle, handle, handle,
        ]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        user32.GetDC.restype = handle
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, handle]
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, handle, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
            handle, ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION),
            wintypes.DWORD,
        ]
        gdi32.CreateCompatibleDC.restype = handle
        gdi32.CreateCompatibleDC.argtypes = [handle]
        gdi32.CreateDIBSection.restype = handle
        gdi32.CreateDIBSection.argtypes = [
            handle, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
            handle, wintypes.DWORD,
        ]
        gdi32.SelectObject.restype = handle
        gdi32.SelectObject.argtypes = [handle, handle]
        gdi32.DeleteObject.argtypes = [handle]
        gdi32.DeleteDC.argtypes = [handle]
        shcore = getattr(ctypes.windll, "shcore", None)

        # No window procedure of our own: Windows' default one is all this window needs.
        cls = WNDCLASSW(lpfnWndProc=ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p).value,
                        lpszClassName="LexiVoxRecordingPill")
        user32.RegisterClassW(ctypes.byref(cls))  # fails harmlessly if already registered
        hwnd = user32.CreateWindowExW(
            WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED,
            cls.lpszClassName, "", WS_POPUP, 0, 0, 1, 1, None, None, None, None)
        if not hwnd:
            log.warning("could not create the recording overlay window")
            return
        pid = os.getpid()
        # layout = (position, x, y, w, h, scale); alpha = fade, 0..1
        view: dict = {"shown": False, "layout": None, "pill": None, "alpha": 0.0,
                      "state": "recording", "last": time.perf_counter()}

        def app_in_front() -> bool:
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(owner))
            return owner.value == pid and fg != hwnd

        def monitor_geometry() -> tuple[tuple[int, int, int, int], float]:
            """Work area and scale of the monitor the user is working on."""
            mon = user32.MonitorFromWindow(user32.GetForegroundWindow(), MONITOR_DEFAULTTOPRIMARY)
            info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            user32.GetMonitorInfoW(mon, ctypes.byref(info))
            dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
            if shcore is not None:
                try:
                    shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                except OSError:
                    pass
            w = info.rcWork
            return (w.left, w.top, w.right, w.bottom), dpi_x.value / 96

        def present(image, x: int, y: int, alpha: float) -> None:
            """Put an RGBA image on screen as the window's whole content."""
            w, h = image.size
            data = image.tobytes("raw", "BGRa")  # premultiplied, as the compositor wants
            screen = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(screen)
            header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32)  # top-down
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(mem, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            try:
                if not bitmap or not bits:
                    return
                ctypes.memmove(bits, data, len(data))
                old = gdi32.SelectObject(mem, bitmap)
                blend = BLENDFUNCTION(0, 0, round(255 * alpha), 1)  # AC_SRC_OVER, AC_SRC_ALPHA
                user32.UpdateLayeredWindow(
                    hwnd, screen, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(w, h)),
                    mem, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
                gdi32.SelectObject(mem, old)
            finally:
                if bitmap:
                    gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(None, screen)

        def render() -> bool:
            """Draw one frame; returns whether the pill is (still) on screen."""
            now = time.perf_counter()
            dt, view["last"] = min(0.25, now - view["last"]), now
            snap = self._snap
            state = snap.get("state", "idle")
            position = self._get_position()
            want = state in STATES and position != "off" and not app_in_front()
            if want:
                if view["alpha"] == 0.0 or view["layout"][0] != position:
                    work, scale = monitor_geometry()  # re-measure each time it appears
                    x, y, w, h = pill_rect(position, work, scale)
                    view["layout"] = (position, x, y, w, h, scale)
                    view["pill"] = PillRenderer(position, w, h, scale)
                view["state"] = state
            elif not view["shown"]:
                return False
            view["alpha"] = max(0.0, min(1.0, view["alpha"] + (dt if want else -dt) / FADE_SECONDS))
            if view["alpha"] == 0.0:  # faded out
                user32.ShowWindow(hwnd, SW_HIDE)
                view["shown"] = False
                return False
            pill = view["pill"]
            _, x, y, w, h, _ = view["layout"]
            recording = want and state == "recording"
            level = self._get_level() if self._get_level is not None else snap.get("level", 0)
            pill.feed(level if recording else 0.0, dt)
            image = pill.frame(view["state"], snap.get("seconds", 0), snap.get("mode", "dictate"), now)
            present(image, x - pill.pad, y - pill.pad, view["alpha"])
            if not view["shown"]:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(
                    hwnd, HWND_TOPMOST, x - pill.pad, y - pill.pad, *image.size, SWP_NOACTIVATE)
                view["shown"] = True
            return True

        msg = wintypes.MSG()
        while True:
            try:
                while True:
                    item = self._queue.get_nowait()
                    if item is None:
                        user32.DestroyWindow(hwnd)
                        return
                    self._snap = {**self._snap, **item}
            except queue.Empty:
                pass
            shown = False
            try:
                shown = render()
            except Exception:
                log.exception("overlay update failed")
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep((FRAME_MS if shown else IDLE_MS) / 1000)
