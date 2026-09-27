"""Small always-on-top "Recording" pill shown while dictating from another app.

It's a Tk window on its own thread, styled so it can never take focus (so it can't
steal the paste), doesn't appear in the taskbar, and lets clicks pass through.
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
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT = 0x8, 0x80, 0x20
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOACTIVATE = 0x10
HWND_TOPMOST = -1
MONITOR_DEFAULTTOPRIMARY = 1

KEY = "#ff00fe"  # transparent colour for the rounded ends
BG, FG = "#120a26", "#f5f0ff"  # brand ink and its light text
STYLE = {  # state -> (dot colour, label); same colour language as the tray icon
    "recording": ("#ff6b00", "Recording"),  # ember
    "transcribing": ("#8b5cff", "Transcribing…"),  # ultraviolet, lifted to read on ink
    "polishing": ("#ccff00", "Polishing…"),  # volt
}
LONG, SHORT = 210, 38  # pill size at 100% scaling
MARGIN = 16


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
    ]


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def pill_rect(position: str, work: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the pill inside a monitor's work area."""
    left, top, right, bottom = work
    long_, short, margin = round(LONG * scale), round(SHORT * scale), round(MARGIN * scale)
    if position == "right":
        return right - short - margin, top + (bottom - top - long_) // 2, short, long_
    return left + (right - left - long_) // 2, bottom - short - margin, long_, short


class RecordingOverlay:
    def __init__(self, get_position: Callable[[], str] = lambda: "bottom") -> None:
        self._get_position = get_position
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

    # ---- Tk thread ----------------------------------------------------------------

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            log.warning("tkinter not available; no recording overlay")
            return
        user32 = ctypes.WinDLL("user32")
        user32.GetParent.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        shcore = getattr(ctypes.windll, "shcore", None)

        root = tk.Tk()
        root.attributes("-alpha", 0.0)  # mapped once (invisibly) so the real window exists
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        root.configure(bg=KEY)
        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, borderwidth=0)
        canvas.pack(fill="both", expand=True)
        root.geometry("1x1+0+0")
        root.update()

        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        user32.ShowWindow(hwnd, SW_HIDE)
        root.attributes("-alpha", 0.96)
        pid = os.getpid()
        view = {"shown": False, "layout": None}  # layout = (position, x, y, w, h)

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

        def draw(position: str, w: int, h: int, scale: float) -> dict:
            """(Re)draw the pill shapes; returns the canvas items that change."""
            canvas.delete("all")
            canvas.configure(width=w, height=h)
            r = min(w, h)
            canvas.create_oval(0, 0, r, r, fill=BG, outline="")
            canvas.create_oval(w - r, h - r, w, h, fill=BG, outline="")
            if position == "right":
                canvas.create_rectangle(0, r // 2, w, h - r // 2, fill=BG, outline="")
            else:
                canvas.create_rectangle(r // 2, 0, w - r // 2, h, fill=BG, outline="")
            d = round(12 * scale)
            c = r // 2
            dot = canvas.create_oval(c - d // 2, c - d // 2, c + d // 2, c + d // 2, fill=STYLE["recording"][0], outline="")
            font = ("Segoe UI", -round(14 * scale), "bold")  # negative = pixels
            if position == "right":
                # Reads top to bottom, like a book spine.
                label = canvas.create_text(c, r + round(2 * scale), anchor="w", angle=-90, fill=FG, font=font)
            else:
                label = canvas.create_text(r, c, anchor="w", fill=FG, font=font)
            meter = canvas.create_rectangle(0, 0, 0, 0, fill=STYLE["recording"][0], outline="")
            return {"dot": dot, "label": label, "meter": meter}

        items: dict = {}

        def layout() -> tuple:
            position = self._get_position()
            work, scale = monitor_geometry()
            x, y, w, h = pill_rect(position, work, scale)
            return position, x, y, w, h, scale

        def render() -> None:
            nonlocal items
            snap = self._snap
            state = snap.get("state", "idle")
            position = self._get_position()
            want = state in STYLE and position != "off" and not app_in_front()
            if not want:
                if view["shown"]:
                    user32.ShowWindow(hwnd, SW_HIDE)
                    view["shown"] = False
                return
            if not view["shown"] or view["layout"][0] != position:
                view["layout"] = layout()  # re-measure each time it appears
                pos, x, y, w, h, scale = view["layout"]
                items = draw(pos, w, h, scale)
                root.geometry(f"{w}x{h}+{x}+{y}")
                root.update_idletasks()
            pos, x, y, w, h, scale = view["layout"]
            colour, text = STYLE[state]
            if state == "recording":
                text = f"{text}  {_fmt(snap.get('seconds', 0))}"  # plain text: Tk can't draw emoji
                if snap.get("mode") == "polish":
                    text += "  · AI"
            canvas.itemconfigure(items["dot"], fill=colour)
            canvas.itemconfigure(items["label"], text=text)
            level = min(1.0, snap.get("level", 0) * 3)
            bar = max(1, round(level * ((h if pos == "right" else w) - 2.2 * min(w, h))))
            t, o = round(3 * scale), min(w, h)
            if pos == "right":  # vertical level bar along the right side
                canvas.coords(items["meter"], w - 2 * t, o, w - t, o + bar)
            else:
                canvas.coords(items["meter"], o, h - 2 * t, o + bar, h - t)
            canvas.itemconfigure(items["meter"], fill=colour, state="normal" if state == "recording" else "hidden")
            if not view["shown"]:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)
                view["shown"] = True

        def tick() -> None:
            try:
                while True:
                    item = self._queue.get_nowait()
                    if item is None:
                        root.destroy()
                        return
                    self._snap = {**self._snap, **item}
            except queue.Empty:
                pass
            try:
                render()
            except Exception:
                log.exception("overlay update failed")
            root.after(100, tick)  # always reschedule, even after an error

        root.after(100, tick)
        root.mainloop()
