"""Small always-on-top "Recording" pill shown while dictating from another app.

It's a Tk window on its own thread, styled so it can never take focus (so it can't
steal the paste), doesn't appear in the taskbar, and lets clicks pass through.
It's hidden whenever the app's own window is in front, since that shows the state.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT = 0x8, 0x80, 0x20
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
HWND_TOPMOST = -1

KEY = "#ff00fe"  # transparent colour for the rounded corners
STYLE = {  # state -> (dot colour, label)
    "recording": ("#ff4b4b", "Recording"),
    "transcribing": ("#f0b429", "Transcribing…"),
    "polishing": ("#b28cff", "Polishing with AI…"),
}
WIDTH, HEIGHT = 210, 38


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


class RecordingOverlay:
    def __init__(self) -> None:
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
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]

        root = tk.Tk()
        root.attributes("-alpha", 0.0)  # mapped once (invisibly) so the real window exists
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        root.configure(bg=KEY)
        canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=KEY, highlightthickness=0)
        canvas.pack()
        r = HEIGHT // 2
        canvas.create_oval(0, 0, HEIGHT, HEIGHT, fill="#1f232a", outline="")
        canvas.create_oval(WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill="#1f232a", outline="")
        canvas.create_rectangle(r, 0, WIDTH - r, HEIGHT, fill="#1f232a", outline="")
        dot = canvas.create_oval(14, 13, 26, 25, fill="#ff4b4b", outline="")
        label = canvas.create_text(36, r, anchor="w", fill="#f2f4f8", font=("Segoe UI", 11, "bold"))
        meter = canvas.create_rectangle(36, HEIGHT - 8, 36, HEIGHT - 5, fill="#ff4b4b", outline="")

        # Place it bottom-centre, just above the taskbar.
        area = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
        x = area.left + (area.right - area.left - WIDTH) // 2
        y = area.bottom - HEIGHT - 24
        root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        root.update()

        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_LAYERED
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        user32.ShowWindow(hwnd, SW_HIDE)
        root.attributes("-alpha", 0.96)
        pid = os.getpid()
        shown = False

        def app_in_front() -> bool:
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(owner))
            return owner.value == pid and fg != hwnd

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

        def render() -> None:
            nonlocal shown
            snap = self._snap
            state = snap.get("state", "idle")
            want = state in STYLE and not app_in_front()
            if want:
                colour, text = STYLE[state]
                if state == "recording":
                    # Plain text only: Tk 8.6 can't draw emoji.
                    text = f"{text}  {_fmt(snap.get('seconds', 0))}"
                    if snap.get("mode") == "polish":
                        text += "  · AI"
                canvas.itemconfigure(dot, fill=colour)
                canvas.itemconfigure(label, text=text)
                level = min(1.0, snap.get("level", 0) * 3)
                canvas.coords(meter, 36, HEIGHT - 8, 36 + max(1, level * (WIDTH - 60)), HEIGHT - 5)
                canvas.itemconfigure(
                    meter, fill=colour, state="normal" if state == "recording" else "hidden"
                )
            if want and not shown:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(
                    hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                )
                shown = True
            elif not want and shown:
                user32.ShowWindow(hwnd, SW_HIDE)
                shown = False

        root.after(100, tick)
        root.mainloop()
