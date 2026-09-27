"""Read-only hardware facts used for display and recommendations."""

from __future__ import annotations

import functools
import os
import platform


@functools.cache
def cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or "CPU"


def cpu_cores() -> int:
    return os.cpu_count() or 1
