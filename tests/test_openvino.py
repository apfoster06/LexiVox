"""OpenVINO engine: device listing, the engine setting, and a real transcription.

The real-model tests are skipped unless openvino-genai and the OpenVINO tiny model are
installed on this machine.
"""

import subprocess
import sys
import threading

import pytest
from conftest import BASE, HDR, HAVE_FFMPEG

from app import services
from app.backends.base import Device, ModelSpec, TranscribeOptions
from app.backends.openvino_backend import OpenVinoBackend
from app.config import data_dir
from app.models.manager import ModelManager

have_ov = pytest.mark.skipif(not OpenVinoBackend.is_installed(), reason="openvino-genai not installed")


def _post(client, path, body):
    return client.post(path, base_url=BASE, headers=HDR, json=body)


def test_engine_setting_round_trip(app, authed, fake_backend):
    fake_backend.devices = [
        Device("gpu", "nvidia", "RTX", "cuda:0"), Device("cpu", "", "CPU", "cpu"),
    ]
    services(app).engine.choices(refresh=True)
    for value in ("faster-whisper|cuda:0", "cpu", ""):
        assert _post(authed, "/api/settings", {"engine": value}).status_code == 200
        assert authed.get("/api/state", base_url=BASE).get_json()["settings"]["engine"] == value
    assert _post(authed, "/api/settings", {"engine": "openvino|GPU"}).status_code == 400
    _post(authed, "/api/settings", {"engine": "cpu"})
    assert services(app).engine.current().device.kind == "cpu"


def test_telemetry_package_never_loads():
    code = (
        "import launcher, sys\n"
        "from app.backends.openvino_backend import OpenVinoBackend\n"
        "import importlib.util\n"
        "if importlib.util.find_spec('openvino_genai'):\n"
        "    import openvino, openvino_genai\n"
        "    OpenVinoBackend().available_devices()\n"
        "assert sys.modules.get('openvino_telemetry') is None\n"
        "try:\n"
        "    import openvino_telemetry\n"
        "    raise SystemExit('telemetry imported')\n"
        "except ImportError:\n"
        "    print('blocked')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert "blocked" in out.stdout


@have_ov
def test_lists_devices():
    devices = OpenVinoBackend().available_devices()
    assert any(d.kind == "cpu" for d in devices)
    assert all(d.kind != "npu" for d in devices)  # hidden until OpenVINO supports it here


@have_ov
@pytest.mark.integration
@pytest.mark.parametrize("engine_id", ["CPU", "GPU"])
def test_real_transcription(tmp_path, engine_id):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    models = ModelManager(data_dir() / "models")
    spec = models.spec("openvino", "tiny")
    if spec is None:
        pytest.skip("OpenVINO tiny model not installed")
    backend = OpenVinoBackend(tmp_path / "cache")
    device = next((d for d in backend.available_devices() if d.engine_id.startswith(engine_id)), None)
    if device is None:
        pytest.skip(f"no {engine_id} device")
    wav = tmp_path / "speech.wav"
    script = (
        "Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{tmp_path / 'raw.wav'}');$s.Speak('Please send the quarterly report by Friday.');$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=60)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tmp_path / "raw.wav"),
                    "-ac", "1", "-ar", "16000", str(wav)], check=True)
    backend.load(ModelSpec("tiny", spec.path), device)
    segs = []
    out = backend.transcribe(wav, TranscribeOptions(language="en", prompt="Glossary: Crowe."),
                             segs.append, threading.Event())
    text = " ".join(s.text for s in out).lower()
    assert "report" in text and "friday" in text
    assert segs == out and all(s.end >= s.start >= 0 for s in out)
