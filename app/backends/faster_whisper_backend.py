"""faster-whisper (CTranslate2): NVIDIA GPUs via CUDA, and a fast int8 CPU path."""

from __future__ import annotations

import gc
import importlib.util
import logging
import os
import threading
from pathlib import Path
from typing import Callable

from .base import Cancelled, Device, ModelSpec, Segment, TranscribeOptions
from .hardware import cpu_name

log = logging.getLogger(__name__)

_dll_dirs_added = False


def add_cuda_dll_dirs() -> None:
    """The nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels (the "nvidia" extra) put their
    DLLs under site-packages/nvidia/*/bin, which Windows doesn't search on its own."""
    global _dll_dirs_added
    if _dll_dirs_added or os.name != "nt":
        return
    _dll_dirs_added = True
    for package in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(package)
        except ModuleNotFoundError:  # no "nvidia" parent package: not an NVIDIA install
            continue
        if spec is None or not spec.submodule_search_locations:
            continue
        for location in spec.submodule_search_locations:
            bin_dir = Path(location) / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None
        self._key: tuple | None = None

    @staticmethod
    def is_installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def available_devices(self) -> list[Device]:
        devices: list[Device] = []
        add_cuda_dll_dirs()
        try:
            import ctranslate2

            count = ctranslate2.get_cuda_device_count()
        except Exception as e:  # missing CUDA runtime DLLs etc.
            log.info("faster-whisper: no CUDA devices (%s)", e)
            count = 0
        for i in range(count):
            devices.append(Device("gpu", "nvidia", f"NVIDIA GPU {i}", f"cuda:{i}"))
        devices.append(Device("cpu", "", cpu_name(), "cpu"))
        return devices

    def load(self, model: ModelSpec, device: Device) -> None:
        key = (str(model.path), device.engine_id)
        if self._key == key:
            return
        self.unload()
        from faster_whisper import WhisperModel

        if device.engine_id.startswith("cuda"):
            index = int(device.engine_id.split(":", 1)[1] or 0)
            self._model = WhisperModel(
                str(model.path), device="cuda", device_index=index, compute_type="float16"
            )
        else:
            self._model = WhisperModel(
                str(model.path), device="cpu", compute_type="int8",
                cpu_threads=os.cpu_count() or 4,
            )
        self._key = key

    def transcribe(
        self,
        audio_path: Path,
        opts: TranscribeOptions,
        on_segment: Callable[[Segment], None],
        cancel: threading.Event,
    ) -> list[Segment]:
        if self._model is None:
            raise RuntimeError("model not loaded")
        segments, _info = self._model.transcribe(
            str(audio_path),
            language=opts.language,
            task="translate" if opts.translate else "transcribe",
            beam_size=5,
            initial_prompt=opts.prompt,
        )
        out: list[Segment] = []
        for s in segments:  # a generator: decoding happens as we iterate
            if cancel.is_set():
                raise Cancelled()
            seg = Segment(float(s.start), float(s.end), s.text.strip())
            out.append(seg)
            on_segment(seg)
        return out

    def unload(self) -> None:
        self._model = None
        self._key = None
        gc.collect()
