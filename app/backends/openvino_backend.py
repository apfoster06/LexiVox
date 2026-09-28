"""OpenVINO GenAI: Intel GPUs (Arc, Iris Xe) and a fast path on any x86 CPU.

The Intel NPU is not offered yet: with OpenVINO 2026.4 the Whisper pipeline fails on
the NPU of a Core Ultra 226V ("roi_end <= max_dim"). Set LEXIVOX_ENABLE_NPU=1 to list it
anyway; the engine falls back to the CPU if it fails.

Compiled models are cached (CACHE_DIR), so the slow first GPU load (~15 s) only
happens once per model and driver version.
"""

from __future__ import annotations

import gc
import importlib.util
import logging
import os
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np

from .base import BackendError, Cancelled, Device, ModelSpec, Segment, TranscribeOptions

log = logging.getLogger(__name__)

# The pipeline handles long audio internally in 30 s windows, but only reports text at
# the end. Feeding it blocks of this length gives live progress and quick cancel.
BLOCK_SECONDS = 120


def read_wav_float(path: Path) -> np.ndarray:
    """16 kHz mono 16-bit WAV -> float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise BackendError("expected 16 kHz mono 16-bit WAV")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, np.int16).astype(np.float32) / 32768.0


class OpenVinoBackend:
    name = "openvino"

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        self._pipe = None
        self._key: tuple | None = None
        self._lock = threading.Lock()

    @staticmethod
    def is_installed() -> bool:
        return importlib.util.find_spec("openvino_genai") is not None

    def available_devices(self) -> list[Device]:
        import openvino as ov

        core = ov.Core()
        devices: list[Device] = []
        for dev in core.available_devices:
            try:
                name = str(core.get_property(dev, "FULL_DEVICE_NAME"))
            except Exception:
                name = dev
            if dev.startswith("GPU"):
                vendor = "intel" if "intel" in name.lower() else ""
                devices.append(Device("gpu", vendor, name, dev))
            elif dev.startswith("NPU"):
                if os.environ.get("LEXIVOX_ENABLE_NPU") == "1":
                    devices.append(Device("npu", "intel", name, dev))
            elif dev == "CPU":
                devices.append(Device("cpu", "", name, dev))
        return devices

    def load(self, model: ModelSpec, device: Device) -> None:
        key = (str(model.path), device.engine_id)
        with self._lock:
            if self._key == key:
                return
            self._unload()
            import openvino_genai as ov_genai

            props = {}
            if self.cache_dir is not None:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                props["CACHE_DIR"] = str(self.cache_dir)
            self._pipe = ov_genai.WhisperPipeline(str(model.path), device.engine_id, **props)
            self._key = key

    def transcribe(
        self,
        audio_path: Path,
        opts: TranscribeOptions,
        on_segment: Callable[[Segment], None],
        cancel: threading.Event,
    ) -> list[Segment]:
        if self._pipe is None:
            raise RuntimeError("model not loaded")
        audio = read_wav_float(audio_path)
        kwargs = {"task": "translate" if opts.translate else "transcribe", "return_timestamps": True}
        if opts.language:
            kwargs["language"] = f"<|{opts.language}|>"
        out: list[Segment] = []
        block = BLOCK_SECONDS * 16000
        for offset in range(0, max(len(audio), 1), block):
            if cancel.is_set():
                raise Cancelled()
            piece = audio[offset:offset + block]
            if len(piece) < 1600:  # < 0.1 s: nothing to transcribe
                continue
            prompt = _prompt(opts.prompt, out)
            if prompt:
                kwargs["initial_prompt"] = prompt
            result = self._pipe.generate(piece.tolist(), **kwargs)
            base = offset / 16000
            chunks = result.chunks or []
            if not chunks and result.texts and result.texts[0].strip():
                chunks = [_Chunk(0.0, len(piece) / 16000, result.texts[0])]
            for c in chunks:
                text = c.text.strip()
                if not text:
                    continue
                seg = Segment(base + max(0.0, float(c.start_ts)), base + max(0.0, float(c.end_ts)), text)
                if seg.end < seg.start:  # the last chunk can come back with end = -1
                    seg.end = base + len(piece) / 16000
                out.append(seg)
                on_segment(seg)
        return out

    def _unload(self) -> None:
        self._pipe = None
        self._key = None
        gc.collect()

    def unload(self) -> None:
        with self._lock:
            self._unload()


class _Chunk:
    def __init__(self, start_ts: float, end_ts: float, text: str):
        self.start_ts, self.end_ts, self.text = start_ts, end_ts, text


def _prompt(vocab: str | None, previous: list[Segment]) -> str | None:
    """Vocabulary plus the end of the previous block, so wording carries across blocks."""
    tail = " ".join(s.text for s in previous[-3:])[-200:]
    parts = [p for p in (vocab, tail) if p]
    return " ".join(parts) if parts else None
