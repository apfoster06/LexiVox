"""System-wide hotkey via the Win32 RegisterHotKey API (ctypes, no extra packages).

RegisterHotKey must be called from the thread that runs the message loop, so all
(re)registration happens on the listener's own thread.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_APP = 0x0312, 0x0012, 0x8000
HOTKEY_ID = 1

_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_ORDER = ["Ctrl", "Alt", "Shift", "Win"]
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "backquote": 0xC0,
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "semicolon": 0xBA, "quote": 0xDE, "bracketleft": 0xDB, "bracketright": 0xDD,
    "backslash": 0xDC,
}


class HotkeyError(ValueError):
    pass


def parse_hotkey(combo: str) -> tuple[int, int, str]:
    """"Ctrl+Alt+Space" -> (modifiers, virtual-key code, normalized text)."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        raise HotkeyError("Use at least one modifier plus a key, e.g. Ctrl+Alt+Space")
    mods, names = 0, []
    for p in parts[:-1]:
        bit = _MODIFIERS.get(p.lower())
        if bit is None:
            raise HotkeyError(f"Unknown modifier: {p}")
        mods |= bit
        names.append(p.capitalize() if p.lower() != "ctrl" else "Ctrl")
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise HotkeyError("Include Ctrl, Alt or Win so normal typing isn't captured")
    key = parts[-1]
    k = key.lower()
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk, key_name = ord(key.upper()), key.upper()
    elif k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        vk, key_name = 0x70 + int(k[1:]) - 1, key.upper()
    elif k in _NAMED_KEYS:
        vk, key_name = _NAMED_KEYS[k], key[0].upper() + key[1:]
    else:
        raise HotkeyError(f"Unsupported key: {key}")
    ordered = sorted(set(names), key=_ORDER.index)
    return mods, vk, "+".join([*ordered, key_name])


class HotkeyListener:
    def __init__(self, on_press: Callable[[], None]):
        self._on_press = on_press
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._request: tuple[str, threading.Event, list] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="hotkey", daemon=True)
            self._thread.start()
            self._ready.wait(5)

    def set(self, combo: str) -> None:
        """Register `combo` ("" to unregister). Raises HotkeyError if Windows refuses it."""
        if combo:
            parse_hotkey(combo)  # validate before touching the current registration
        done, result = threading.Event(), []
        with self._lock:
            self._request = (combo, done, result)
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
            if not done.wait(5):
                raise HotkeyError("The hotkey listener didn't respond")
        if result and result[0]:
            raise HotkeyError(result[0])

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the message queue
        self._ready.set()
        registered = False
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                threading.Thread(target=self._fire, daemon=True).start()
            elif msg.message == WM_APP and self._request:
                combo, done, result = self._request
                self._request = None
                if registered:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    registered = False
                error = None
                if combo:
                    mods, vk, _ = parse_hotkey(combo)
                    if user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                        registered = True
                    else:
                        error = f"{combo} is already used by another app. Pick a different shortcut."
                result.append(error)
                done.set()
        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _fire(self) -> None:
        try:
            self._on_press()
        except Exception:
            log.exception("hotkey handler failed")
