"""Copy text to the Windows clipboard (ctypes, no extra packages).

The text is marked so Windows won't upload it to the cloud clipboard ("sync across
devices"); it can still appear in the local Win+V history.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class ClipboardError(OSError):
    pass


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    return user32, kernel32


def _global(kernel32, data: bytes):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ClipboardError("GlobalAlloc failed")
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def copy_text(text: str) -> None:
    user32, kernel32 = _api()
    for _ in range(20):  # another app may hold the clipboard briefly
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ClipboardError("The clipboard is busy")
    try:
        user32.EmptyClipboard()
        handle = _global(kernel32, text.encode("utf-16-le") + b"\0\0")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ClipboardError("SetClipboardData failed")
        no_cloud = user32.RegisterClipboardFormatW("CanUploadToCloudClipboard")
        if no_cloud:
            flag = _global(kernel32, (0).to_bytes(4, "little"))
            if not user32.SetClipboardData(no_cloud, flag):
                kernel32.GlobalFree(flag)
    finally:
        user32.CloseClipboard()


def read_text() -> str:
    """For tests and diagnostics."""
    user32, kernel32 = _api()
    if not user32.OpenClipboard(None):
        raise ClipboardError("The clipboard is busy")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
