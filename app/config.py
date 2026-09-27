"""Per-machine paths and settings.

Everything lives under %LOCALAPPDATA%\\CroweVibeWhisper (never in the repo), so each
machine keeps its own hardware choice, models, history and logs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "Crowe Vibe Whisper"
APP_DIR_NAME = "CroweVibeWhisper"

DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following transcript. Start with a one-paragraph overview, "
    "then list the key points and any action items as bullet points.\n\n"
    "Transcript:\n{transcript}"
)

log = logging.getLogger(__name__)


def data_dir() -> Path:
    override = os.environ.get("CVW_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_DIR_NAME


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def transcripts(self) -> Path:
        return self.root / "transcripts"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def temp(self) -> Path:
        return self.root / "tmp"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    def ensure(self) -> None:
        for p in (self.root, self.models, self.transcripts, self.logs, self.temp):
            p.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    model: str = ""  # "" = use the hardware recommendation
    language: str = "auto"
    translate: bool = False
    backend: str = ""  # "" = auto-select
    device: str = ""
    force_cpu: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    summary_prompt: str = DEFAULT_SUMMARY_PROMPT
    log_retention_days: int = 14
    max_upload_mb: int = 4096
    theme: str = "system"
    hotkey: str = "Ctrl+Alt+Space"  # dictation toggle; "" = off
    benchmark: dict = field(default_factory=dict)
    first_run_done: bool = False


class SettingsStore:
    """Thread-safe JSON settings file. Unknown keys and wrong types fall back to defaults."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        defaults = Settings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defaults
        except (OSError, ValueError) as e:
            log.warning("config unreadable, using defaults: %s", e)
            return defaults
        if not isinstance(raw, dict):
            return defaults
        values = {}
        for f in fields(Settings):
            if f.name in raw and _same_type(raw[f.name], getattr(defaults, f.name)):
                values[f.name] = raw[f.name]
        return Settings(**values)

    def get(self) -> Settings:
        with self._lock:
            return Settings(**asdict(self._settings))

    def update(self, **changes) -> Settings:
        with self._lock:
            current = asdict(self._settings)
            for key, value in changes.items():
                if key not in current:
                    raise KeyError(key)
                if not _same_type(value, current[key]):
                    raise TypeError(f"{key} has the wrong type")
                current[key] = value
            self._settings = Settings(**current)
            self._save()
            return Settings(**current)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._settings), indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _same_type(value, default) -> bool:
    # bool is a subclass of int, so check it explicitly.
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(value, bool) and isinstance(default, bool)
    return isinstance(value, type(default))
