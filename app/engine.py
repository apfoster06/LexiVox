"""Backend/device selection and transcription with automatic CPU fallback."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backends.base import Backend, Cancelled, Device, Segment, TranscribeOptions
from .config import SettingsStore
from .models.manager import ModelManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Choice:
    backend: str
    device: Device

    @property
    def key(self) -> str:
        return f"{self.backend}|{self.device.engine_id}"

    def to_dict(self) -> dict:
        return {"backend": self.backend, "device": self.device.to_dict(), "key": self.key}


class ModelMissing(Exception):
    def __init__(self, backend: str, model: str):
        super().__init__(f"Model '{model}' is not installed for {backend}. Download it first.")
        self.backend = backend
        self.model = model


def auto_order(choices: list[Choice], cpu_winner: str | None = None) -> list[Choice]:
    """Default preference order (see README):

    1. NVIDIA GPU -> faster-whisper CUDA   2. Intel GPU -> OpenVINO GPU
    3. Intel NPU -> OpenVINO NPU (the caller only lists NPUs that passed a load test)
    4. AMD GPU -> whisper.cpp              5. CPU (benchmark winner, else faster-whisper)
    """

    def rank(c: Choice) -> tuple[int, int]:
        d = c.device
        if d.kind == "gpu" and d.vendor == "nvidia" and c.backend == "faster-whisper":
            return (0, 0)
        if d.kind == "gpu" and d.vendor == "intel" and c.backend == "openvino":
            return (1, 0)
        if d.kind == "npu" and c.backend == "openvino":
            return (2, 0)
        if d.kind == "gpu" and c.backend == "whispercpp":
            return (3, 0)
        if d.kind == "cpu":
            if cpu_winner:
                return (4, 0 if c.backend == cpu_winner else 1)
            return (4, {"faster-whisper": 0, "openvino": 1}.get(c.backend, 2))
        return (5, 0)  # anything else (e.g. unknown vendor GPU): last resort

    return sorted(choices, key=rank)


def recommended_model(choice: Choice | None, cores: int) -> str:
    """GPU/NPU: large-v3-turbo; fast CPU (8+ cores): small; slow CPU: base."""
    if choice is not None and choice.device.kind in ("gpu", "npu"):
        return "large-v3-turbo"
    return "small" if cores >= 8 else "base"


def effective_model(engine: "Engine", cores: int) -> str:
    """The model the user picked, or the hardware recommendation."""
    chosen = engine.settings.get().model
    if chosen in engine.models.catalog:
        return chosen
    return recommended_model(engine.current(), cores)


class Engine:
    def __init__(self, backends: dict[str, Backend], models: ModelManager, settings: SettingsStore):
        self.backends = backends
        self.models = models
        self.settings = settings
        self._choices: list[Choice] | None = None
        self._run_lock = threading.Lock()
        self._probe_lock = threading.Lock()

    def choices(self, refresh: bool = False) -> list[Choice]:
        with self._probe_lock:
            return self._probe(refresh)

    def _probe(self, refresh: bool) -> list[Choice]:
        if self._choices is None or refresh:
            found: list[Choice] = []
            for name, backend in self.backends.items():
                try:
                    found.extend(Choice(name, d) for d in backend.available_devices())
                except Exception:
                    log.exception("device probe failed for %s", name)
            self._choices = found
        return list(self._choices)

    def _find(self, key: str) -> Choice | None:
        return next((c for c in self.choices() if c.key == key), None)

    def cpu_choice(self, prefer_backend: str | None = None) -> Choice | None:
        cpus = [c for c in self.choices() if c.device.kind == "cpu"]
        if prefer_backend:
            cpus.sort(key=lambda c: c.backend != prefer_backend)
        else:
            winner = self.settings.get().benchmark.get("cpu_winner")
            cpus = [c for c in auto_order(cpus, winner)]
        return cpus[0] if cpus else None

    def current(self) -> Choice | None:
        s = self.settings.get()
        if s.force_cpu:
            return self.cpu_choice()
        if s.backend:
            manual = self._find(f"{s.backend}|{s.device}")
            if manual:
                return manual
        best = s.benchmark.get("best")
        if best:
            found = self._find(best)
            if found:
                return found
        ordered = auto_order(self.choices(), s.benchmark.get("cpu_winner"))
        return ordered[0] if ordered else None

    def transcribe(
        self,
        audio: Path,
        model: str,
        opts: TranscribeOptions,
        on_segment: Callable[[Segment], None],
        on_restart: Callable[[str], None],
        cancel: threading.Event,
        choice: Choice | None = None,
    ) -> tuple[list[Segment], Choice, str | None]:
        """Returns (segments, choice actually used, notice for the user or None)."""
        choice = choice or self.current()
        if choice is None:
            raise RuntimeError("No transcription engine is installed. Run scripts/install.ps1.")
        with self._run_lock:
            try:
                return self._run(choice, audio, model, opts, on_segment, cancel), choice, None
            except (Cancelled, ModelMissing):
                raise
            except Exception as e:
                if choice.device.kind == "cpu":
                    raise
                log.warning("%s on %s failed, retrying on CPU: %s", choice.backend, choice.device.name, e)
                fallback = self.cpu_choice(prefer_backend=choice.backend)
                if fallback is None:
                    raise
                self.backends[choice.backend].unload()
                notice = f"{choice.device.kind.upper()} failed, finished on CPU. See Settings."
                on_restart(notice)
                return self._run(fallback, audio, model, opts, on_segment, cancel), fallback, notice

    def _run(self, choice, audio, model, opts, on_segment, cancel) -> list[Segment]:
        spec = self.models.spec(choice.backend, model)
        if spec is None:
            raise ModelMissing(choice.backend, model)
        backend = self.backends[choice.backend]
        backend.load(spec, choice.device)
        if cancel.is_set():
            raise Cancelled()
        return backend.transcribe(audio, opts, on_segment, cancel)
