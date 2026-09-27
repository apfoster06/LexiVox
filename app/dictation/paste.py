"""Paste into whatever window has focus by sending Ctrl+V (ctypes, no extra packages)."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

VK_CONTROL, VK_V = 0x11, 0x56
KEYEVENTF_KEYUP = 0x2
# Ctrl, Alt, Shift and both Win keys: pasting while any is still held would send a
# different shortcut (e.g. Win+Ctrl+V opens the sound panel).
_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return user32


def foreground_is_own_window(user32) -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return True  # nothing focused (e.g. the lock screen): don't type blindly
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def wait_for_modifiers_released(user32, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS):
            return True
        time.sleep(0.03)
    return False


def paste() -> bool:
    """Send Ctrl+V to the focused window. Returns False if it was skipped."""
    user32 = _user32()
    if foreground_is_own_window(user32) or not wait_for_modifiers_released(user32):
        return False
    time.sleep(0.05)  # let the clipboard owner settle
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    return True
