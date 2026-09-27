from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app import create_app
from app.backends.base import Cancelled, Device, Segment
from app.config import Paths, SettingsStore
from app.models.manager import ModelManager, load_catalog

PORT = 5555
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-launch-token"
HDR = {"X-Requested-With": "CroweVibeWhisper"}

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not on PATH")


class FakeBackend:
    """Stands in for a real engine. `fail_on` makes loads on that device kind fail."""

    def __init__(self, name="faster-whisper", devices=None, fail_on=None, segments=None):
        self.name = name
        self.devices = devices or [Device("cpu", "", "Test CPU", "cpu")]
        self.fail_on = fail_on
        self.segments = segments or [Segment(0.0, 1.0, "hello"), Segment(1.0, 2.0, "world")]
        self.loaded = None
        self.calls: list[str] = []

    def available_devices(self):
        return list(self.devices)

    def load(self, model, device):
        self.calls.append(f"load:{device.engine_id}")
        if self.fail_on and device.kind == self.fail_on:
            raise RuntimeError("driver exploded")
        self.loaded = (model, device)

    def transcribe(self, audio_path, opts, on_segment, cancel):
        self.last_opts = opts
        out = []
        for s in self.segments:
            if cancel.is_set():
                raise Cancelled()
            out.append(s)
            on_segment(s)
        return out

    def unload(self):
        self.loaded = None


def install_fake_model(models: ModelManager, backend: str, model: str) -> None:
    """Mark a model as installed without real files (writes files matching a tiny catalog)."""
    import json

    v = models.variant(backend, model)
    root = models.model_dir(backend, model)
    root.mkdir(parents=True, exist_ok=True)
    for f in v.files:
        (root / f.path).write_bytes(b"\0" * f.size)
    (root / ".verified.json").write_text(json.dumps({"revision": v.revision}))


@pytest.fixture
def paths(tmp_path) -> Paths:
    p = Paths(tmp_path / "data")
    p.ensure()
    return p


@pytest.fixture
def settings(paths) -> SettingsStore:
    return SettingsStore(paths.config_file)


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


class FakeRecorder:
    """Pretends to record by copying a prepared WAV file."""

    max_seconds = 900

    def __init__(self):
        self.source: Path | None = None
        self.duration = 2.0
        self.level = 0.0
        self.recording = False
        self.fail: Exception | None = None

    def start(self, device_name: str = ""):
        if self.fail:
            raise self.fail
        self.device_name = device_name
        self.recording = True

    def seconds(self):
        return 0.5 if self.recording else 0.0

    def stop(self, dst: Path) -> float:
        self.recording = False
        if self.source is not None:
            shutil.copy(self.source, dst)
        else:
            dst.write_bytes(b"")
        return self.duration

    def cancel(self):
        self.recording = False


@pytest.fixture
def fake_recorder() -> FakeRecorder:
    return FakeRecorder()


@pytest.fixture
def clipboard() -> list[str]:
    return []


@pytest.fixture
def pastes() -> list[int]:
    return []


class FakePolisher:
    def __init__(self):
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, text: str) -> str:
        self.calls.append(text)
        if self.fail:
            raise self.fail
        return f"Polished: {text}"


@pytest.fixture
def polisher() -> FakePolisher:
    return FakePolisher()


@pytest.fixture
def app(paths, settings, fake_backend, fake_recorder, clipboard, pastes, polisher):
    models = ModelManager(paths.models, load_catalog())
    application = create_app(
        paths, settings, TOKEN, port=PORT, backends={"faster-whisper": fake_backend},
        models=models, start_worker=True, recorder=fake_recorder, copy=clipboard.append,
        paste=lambda: pastes.append(1) or True, polisher=polisher,
    )
    application.extensions["cvw"].dictation.beeps = False
    yield application
    application.extensions["cvw"].jobs.stop()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authed(client):
    resp = client.get("/?launch=" + TOKEN, base_url=BASE)
    assert resp.status_code == 303
    return client


@pytest.fixture
def sine_wav(tmp_path) -> Path:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-ar", "16000", str(out)],
        check=True,
    )
    return out


def wait_for(predicate, timeout=10.0):
    done = threading.Event()
    end = __import__("time").monotonic() + timeout
    while __import__("time").monotonic() < end:
        if predicate():
            return True
        done.wait(0.05)
    return False


os.environ.setdefault("HF_HUB_OFFLINE", "1")
