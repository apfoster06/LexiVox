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
    """"Ctrl+Alt+Space" -> (modifiers, virtual-key code, normalized text).

    Modifier-only chords like "Ctrl+Win" return vk 0; they fire when released with no
    other key pressed in between.
    """
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        raise HotkeyError("Use at least one modifier plus a key, e.g. Ctrl+Alt+Space")
    if all(p.lower() in _MODIFIERS for p in parts):
        mods = 0
        for p in parts:
            mods |= _MODIFIERS[p.lower()]
        if mods in (MOD_ALT | MOD_SHIFT, MOD_CONTROL | MOD_SHIFT):
            raise HotkeyError("That combo switches keyboard layouts in Windows. Pick another.")
        names = [n for n in _ORDER if _MODIFIERS[n.lower()] & mods]
        if len(names) < 2:
            raise HotkeyError("Use at least two modifier keys, e.g. Ctrl+Win")
        return mods, 0, "+".join(names)
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


WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
MASK_KEY = 0xE8  # unassigned virtual key; tapping it stops Win from opening Start
_MOD_VKS = {
    0x11: MOD_CONTROL, 0xA2: MOD_CONTROL, 0xA3: MOD_CONTROL,
    0x12: MOD_ALT, 0xA4: MOD_ALT, 0xA5: MOD_ALT,
    0x10: MOD_SHIFT, 0xA0: MOD_SHIFT, 0xA1: MOD_SHIFT,
    0x5B: MOD_WIN, 0x5C: MOD_WIN,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class ModifierChord:
    """Fires when exactly `mods` are held and then released with no other key in between.

    So Ctrl+Win fires, but Ctrl+Win+D (switch desktop) doesn't.
    """

    def __init__(
        self,
        mods: int,
        fire: Callable[[], None],
        mask: Callable[[], None],
        is_pressed: Callable[[int], bool] | None = None,
    ):
        self.mods = mods
        self.fire = fire
        self.mask = mask
        # Key-ups can be missed (e.g. Win+L switches to the lock screen), so stale
        # entries are dropped using the real key state.
        self.is_pressed = is_pressed
        self.down: set[int] = set()
        self.armed = False
        self.spoiled = False

    def held(self) -> int:
        bits = 0
        for vk in self.down:
            bits |= _MOD_VKS[vk]
        return bits

    def on_key(self, vk: int, is_down: bool) -> None:
        if vk == MASK_KEY:
            return
        if vk not in _MOD_VKS:
            if is_down and self.down:
                self.spoiled = True
            return
        if is_down:
            if self.is_pressed is not None and vk not in self.down:
                stale = {k for k in self.down if not self.is_pressed(k)}
                if stale:
                    self.down -= stale
                    if not self.down:
                        self.armed = self.spoiled = False
            self.down.add(vk)
            if self.held() == self.mods:
                if not self.armed:
                    self.armed, self.spoiled = True, False
                    if self.mods & MOD_WIN:
                        self.mask()
            elif self.armed:
                self.spoiled = True  # an extra modifier joined in
            return
        if self.armed and not self.spoiled and self.held() == self.mods:
            self.fire()
            self.spoiled = True  # releasing the other modifier must not fire again
        self.down.discard(vk)
        if not self.down:
            self.armed = self.spoiled = False


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
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        hookproc_t = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc_t, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the message queue
        self._ready.set()

        registered = False
        hook = None
        chord: ModifierChord | None = None

        def mask() -> None:
            user32.keybd_event(MASK_KEY, 0, 0, 0)
            user32.keybd_event(MASK_KEY, 0, 2, 0)

        def on_hook(n_code, w_param, l_param):
            if n_code == 0 and chord is not None:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                try:
                    chord.on_key(kb.vkCode, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN))
                except Exception:
                    log.exception("keyboard hook failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_proc = hookproc_t(on_hook)  # keep a reference so it isn't garbage collected

        def fire_async() -> None:
            threading.Thread(target=self._fire, daemon=True).start()

        def clear() -> None:
            nonlocal registered, hook, chord
            if registered:
                user32.UnregisterHotKey(None, HOTKEY_ID)
                registered = False
            if hook:
                user32.UnhookWindowsHookEx(hook)
                hook = None
            chord = None

        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                fire_async()
            elif msg.message == WM_APP and self._request:
                combo, done, result = self._request
                self._request = None
                clear()
                error = None
                if combo:
                    mods, vk, _ = parse_hotkey(combo)
                    if vk == 0:
                        chord = ModifierChord(
                            mods, fire_async, mask,
                            lambda k: bool(user32.GetAsyncKeyState(k) & 0x8000),
                        )
                        hook = user32.SetWindowsHookExW(
                            WH_KEYBOARD_LL, hook_proc, kernel32.GetModuleHandleW(None), 0
                        )
                        if not hook:
                            chord = None
                            error = "Windows refused the keyboard hook for that shortcut."
                    elif user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                        registered = True
                    else:
                        error = f"{combo} is already used by another app. Pick a different shortcut."
                result.append(error)
                done.set()
        clear()

    def _fire(self) -> None:
        try:
            self._on_press()
        except Exception:
            log.exception("hotkey handler failed")
