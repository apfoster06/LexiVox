"""ffmpeg/ffprobe helpers.

Subprocesses always get argument lists (never a shell). Inputs are server-generated
absolute paths, passed as "file:<path>" after -i with the protocol whitelist set to
"file", so a crafted upload (e.g. an HLS playlist renamed to .mp3) can't make ffmpeg
open network URLs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

ALLOWED_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff",
    ".aif", ".amr", ".webm", ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".wmv", ".mpeg",
    ".mpg", ".3gp", ".ts", ".mts",
})

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class AudioError(Exception):
    pass


def extension_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise AudioError(f"{name} was not found on PATH. Install ffmpeg and restart the app.")
    return path


def _input_args(path: Path) -> list[str]:
    return ["-protocol_whitelist", "file", "-i", f"file:{path}"]


def probe(path: Path, timeout: float = 60) -> float:
    """Check the file really contains audio. Returns its duration in seconds (0 if unknown)."""
    cmd = [
        _tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", *_input_args(path),
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False, creationflags=_NO_WINDOW
        )
    except subprocess.TimeoutExpired as e:
        raise AudioError("Timed out reading the file") from e
    if out.returncode != 0:
        raise AudioError("Not a readable audio or video file")
    try:
        info = json.loads(out.stdout or b"{}")
    except ValueError as e:
        raise AudioError("Not a readable audio or video file") from e
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise AudioError("The file has no audio track")
    try:
        return max(0.0, float((info.get("format") or {}).get("duration", 0)))
    except (TypeError, ValueError):
        return 0.0


def to_wav16k(src: Path, dst: Path, cancel: threading.Event | None = None) -> None:
    """Convert the first audio stream to 16 kHz mono 16-bit WAV."""
    cmd = [
        _tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        *_input_args(src),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", f"file:{dst}",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    try:
        while True:
            try:
                _, err = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.communicate()
                    raise AudioError("cancelled")
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        raise AudioError("ffmpeg could not convert the file: " + err.decode("utf-8", "replace")[-300:])
