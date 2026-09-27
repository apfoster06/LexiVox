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


def make_icon(state: str, size: int = 64):
    """The app icon, drawn in code: a coloured circle with a white microphone."""
    from PIL import Image, ImageDraw

    s = size / 64  # the drawing below is laid out on a 64 px grid
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2 * s, 2 * s, size - 2 * s, size - 2 * s), fill=COLORS.get(state, COLORS["idle"]))
    white = (255, 255, 255)
    w = max(1, round(4 * s))
    d.rounded_rectangle((25 * s, 12 * s, 39 * s, 38 * s), radius=7 * s, fill=white)  # mic head
    d.arc((18 * s, 22 * s, 46 * s, 46 * s), start=0, end=180, fill=white, width=w)  # holder
    d.line((32 * s, 46 * s, 32 * s, 53 * s), fill=white, width=w)
    d.line((24 * s, 53 * s, 40 * s, 53 * s), fill=white, width=w)
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
