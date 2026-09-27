"""The small interface every inference engine implements.

The rest of the app only sees Segment objects, never engine-specific types.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True)
class Device:
    kind: str  # "gpu", "npu" or "cpu"
    vendor: str  # "intel", "nvidia", "amd" or ""
    name: str  # human readable, e.g. "Intel(R) Arc(TM) 130V GPU"
    engine_id: str  # what the engine wants, e.g. "GPU", "NPU.0", "cuda:0", "cpu"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ModelSpec:
    name: str  # logical name from the catalog, e.g. "small"
    path: Path  # verified local folder for this backend


@dataclass(frozen=True)
class TranscribeOptions:
    language: str | None = None  # None = auto-detect
    translate: bool = False


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Cancelled(Exception):
    pass


class BackendError(Exception):
    pass


class Backend(Protocol):
    name: str

    def available_devices(self) -> list[Device]: ...

    def load(self, model: ModelSpec, device: Device) -> None: ...

    def transcribe(
        self,
        audio_path: Path,
        opts: TranscribeOptions,
        on_segment: Callable[[Segment], None],
        cancel: threading.Event,
    ) -> list[Segment]: ...

    def unload(self) -> None: ...
