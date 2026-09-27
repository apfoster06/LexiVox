"""Discovers which inference engines are installed on this machine."""

from __future__ import annotations

from pathlib import Path

from .base import Backend


def installed_backends(cache_dir: Path | None = None) -> dict[str, Backend]:
    """Engines whose packages are installed (see the cpu/nvidia/intel extras)."""
    found: dict[str, Backend] = {}
    from .faster_whisper_backend import FasterWhisperBackend
    from .openvino_backend import OpenVinoBackend

    if FasterWhisperBackend.is_installed():
        found["faster-whisper"] = FasterWhisperBackend()
    if OpenVinoBackend.is_installed():
        found["openvino"] = OpenVinoBackend(cache_dir)
    return found
