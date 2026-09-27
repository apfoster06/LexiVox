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


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.ascontiguousarray(samples, dtype=np.int16).tobytes())


class MicRecorder:
    """Records into a growing buffer that can be read while recording continues,
    so finished stretches of speech can be transcribed before you stop."""

    def __init__(self, max_seconds: float = 900):
        self.max_seconds = max_seconds
        self._stream = None
        self._buf = np.zeros(0, np.int16)
        self._len = 0
        self.rate = 16000
        self._lock = threading.Lock()
        self.level = 0.0  # last input peak, 0..1, for the UI meter

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def total(self) -> int:
        with self._lock:
            return self._len

    def seconds(self) -> float:
        return self.total() / self.rate

    def read(self, start: int, end: int) -> np.ndarray:
        with self._lock:
            return self._buf[start:min(end, self._len)].copy()

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
        self.rate = int(info.get("default_samplerate") or 16000)
        with self._lock:
            self._buf = np.zeros(self.rate * 60, np.int16)  # grows as needed
            self._len = 0
        try:
            self._stream = sd.InputStream(
                device=device, samplerate=self.rate, channels=1, dtype="int16",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise RecorderError(
                "Could not open the microphone. Check Windows Settings > Privacy > Microphone."
            ) from e

    def _callback(self, indata, frames, time_info, status) -> None:
        data = indata[:, 0]
        with self._lock:
            room = int(self.max_seconds * self.rate) - self._len
            if room > 0:
                data = data[:room]
                need = self._len + len(data)
                if need > len(self._buf):
                    grown = np.zeros(max(need, len(self._buf) * 2), np.int16)
                    grown[:self._len] = self._buf[:self._len]
                    self._buf = grown
                self._buf[self._len:need] = data
                self._len = need
        self.level = float(np.abs(indata).max()) / 32768.0

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self.level = 0.0

    def stop(self) -> float:
        """Stop recording; the audio stays readable until the next start. Returns seconds."""
        self._close()
        return self.seconds()

    def cancel(self) -> None:
        self._close()
        with self._lock:
            self._buf = np.zeros(0, np.int16)
            self._len = 0
