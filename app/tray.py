"""System tray icon: keeps the app (and its dictation hotkey) alive when the window is closed.

Blue = ready, red = recording, amber = transcribing.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .config import APP_NAME

log = logging.getLogger(__name__)

COLORS = {
    "idle": (15, 95, 215), "recording": (214, 40, 40),
    "transcribing": (212, 150, 20), "polishing": (130, 80, 200),
}
TITLES = {
    "idle": "ready", "recording": "recording…", "transcribing": "transcribing…",
    "polishing": "polishing with AI…",
}


def make_icon(state: str):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=COLORS.get(state, COLORS["idle"]))
    white = (255, 255, 255)
    d.rounded_rectangle((25, 12, 39, 38), radius=7, fill=white)  # microphone head
    d.arc((18, 22, 46, 46), start=0, end=180, fill=white, width=4)  # holder
    d.line((32, 46, 32, 53), fill=white, width=4)
    d.line((24, 53, 40, 53), fill=white, width=4)
    return img


class Tray:
    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
    ):
        self._on_show = on_show
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._state = "idle"
        self._hotkey = ""
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            log.warning("pystray not available; no tray icon")
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Show window", lambda: self._on_show(), default=True),
            pystray.MenuItem(
                lambda item: "Stop dictation" if self._state == "recording" else "Start dictation",
                lambda: self._on_toggle(),
                enabled=lambda item: self._state in ("idle", "recording"),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self._on_quit()),
        )
        self.icon = pystray.Icon("CroweVibeWhisper", make_icon("idle"), APP_NAME, menu)
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        return True

    def update(self, snapshot: dict) -> None:
        state = snapshot.get("state", "idle")
        hotkey = snapshot.get("hotkey", self._hotkey) or ""
        if (state, hotkey) == (self._state, self._hotkey) and self.icon is not None:
            return  # frequent level updates; only redraw on changes
        self._state, self._hotkey = state, hotkey
        if self.icon is None:
            return
        self.icon.icon = make_icon(self._state)
        hint = f" ({self._hotkey})" if self._hotkey else ""
        self.icon.title = f"{APP_NAME}: {TITLES.get(self._state, '')}{hint}"
        self.icon.update_menu()

    def notify(self, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message, APP_NAME)
            except Exception:
                log.debug("tray notification failed", exc_info=True)

    def stop(self) -> None:
        if self.icon is not None:
            self.icon.stop()
