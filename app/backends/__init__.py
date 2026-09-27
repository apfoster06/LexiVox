"""Discovers which inference engines are installed on this machine."""

from __future__ import annotations

from .base import Backend


def installed_backends() -> dict[str, Backend]:
    found: dict[str, Backend] = {}
    from .faster_whisper_backend import FasterWhisperBackend

    if FasterWhisperBackend.is_installed():
        found["faster-whisper"] = FasterWhisperBackend()
    return found
