"""Microphone capture with sounddevice (PortAudio).

Records at the device's native rate; the result goes through the same ffmpeg
conversion as uploaded files, so odd sample rates are handled in one place.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np


class RecorderError(RuntimeError):
    pass


def _input_devices() -> list[tuple[int, str]]:
    """(index, name) of inputs. WASAPI gives full, de-duplicated names on Windows;
    other APIs list the same mic several times (MME even truncates names)."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    devices = sd.query_devices()
    wasapi = next((i for i, a in enumerate(apis) if a["name"] == "Windows WASAPI"), None)
    chosen_api = wasapi if wasapi is not None else sd.default.hostapi
    return [
        (i, d["name"]) for i, d in enumerate(devices)
        if d["max_input_channels"] > 0 and d["hostapi"] == chosen_api
    ]


def list_microphones() -> list[str]:
    try:
        return [name for _, name in _input_devices()]
    except Exception:
        return []


class MicRecorder:
    def __init__(self, max_seconds: float = 900):
        self.max_seconds = max_seconds
        self._stream = None
        self._frames: list[np.ndarray] = []
        self._rate = 16000
        self._lock = threading.Lock()
        self.level = 0.0  # last input peak, 0..1, for the UI meter

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def seconds(self) -> float:
        with self._lock:
            return sum(len(f) for f in self._frames) / self._rate

    def start(self, device_name: str = "") -> None:
        """Start recording from `device_name` ("" or not found = Windows default)."""
        import sounddevice as sd

        device = None
        if device_name:
            device = next((i for i, n in _input_devices() if n == device_name), None)
        try:
            info = sd.query_devices(device if device is not None else sd.default.device[0])
        except Exception as e:
            raise RecorderError("No microphone was found") from e
        self._rate = int(info.get("default_samplerate") or 16000)
        self._frames = []
        try:
            self._stream = sd.InputStream(
                device=device, samplerate=self._rate, channels=1, dtype="int16",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise RecorderError(
                "Could not open the microphone. Check Windows Settings > Privacy > Microphone."
            ) from e

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            if sum(len(f) for f in self._frames) / self._rate < self.max_seconds:
                self._frames.append(indata.copy())
        self.level = float(np.abs(indata).max()) / 32768.0

    def stop(self, dst: Path) -> float:
        """Stop and write a WAV file. Returns the duration in seconds."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            audio = np.concatenate(self._frames) if self._frames else np.zeros((0, 1), np.int16)
            self._frames = []
        with wave.open(str(dst), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self._rate)
            w.writeframes(audio.tobytes())
        self.level = 0.0
        return len(audio) / self._rate

    def cancel(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            self._frames = []
