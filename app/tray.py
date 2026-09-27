"""System tray icon: keeps the app (and its dictation hotkey) alive when the window is closed."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .config import APP_NAME

log = logging.getLogger(__name__)

TITLES = {
    "idle": "ready", "recording": "recording…", "transcribing": "transcribing…",
    "polishing": "polishing with AI…",
}


def make_icon(state: str, size: int = 64):
    """The app icon (assets/brand/icon.png) recoloured for the dictation state:
    fuchsia = ready, ember = recording, ultraviolet = transcribing, volt = polishing."""
    from .brand import make_icon as brand_icon

    return brand_icon(state, size)


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
