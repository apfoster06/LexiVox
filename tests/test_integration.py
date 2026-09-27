"""Real transcription through the HTTP API with the tiny model on CPU.

Skipped unless the tiny faster-whisper model is installed in this machine's data dir
and ffmpeg is on PATH. Uses the benchmark sample if present; otherwise Windows TTS.
"""

import os
import subprocess
from pathlib import Path

import pytest
from conftest import BASE, HDR, HAVE_FFMPEG, PORT, TOKEN, wait_for

from app import create_app, services
from app.audio import extension_allowed
from app.backends import installed_backends
from app.config import Paths, SettingsStore, data_dir
from app.models.manager import ModelManager

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _sample(tmp_path) -> Path:
    shipped = sorted(p for p in (ROOT / "assets" / "benchmark").iterdir() if extension_allowed(p.name))
    if shipped:
        return shipped[0]
    if os.name != "nt":
        pytest.skip("no benchmark sample and no Windows TTS")
    out = tmp_path / "tts.wav"
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{out}');"
        "$s.Speak('The quick brown fox jumps over the lazy dog. Please send the report by Friday.');"
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=60)
    return out


def test_real_transcription_tiny_cpu(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    models = ModelManager(data_dir() / "models")
    if not models.is_installed("faster-whisper", "tiny"):
        pytest.skip("tiny faster-whisper model not installed")
    backends = {k: v for k, v in installed_backends().items() if k == "faster-whisper"}

    paths = Paths(tmp_path / "data")
    settings = SettingsStore(paths.config_file)
    settings.update(force_cpu=True)
    app = create_app(paths, settings, TOKEN, port=PORT, backends=backends, models=models)
    try:
        c = app.test_client()
        assert c.get("/?launch=" + TOKEN, base_url=BASE).status_code == 303
        sample = _sample(tmp_path)
        resp = c.post(
            "/api/jobs", base_url=BASE, headers=HDR, content_type="multipart/form-data",
            data={"files": [(sample.open("rb"), sample.name)], "model": "tiny", "language": "en"},
        )
        assert resp.status_code == 201, resp.get_json()
        job_id = resp.get_json()["jobs"][0]["id"]
        get = lambda: c.get(f"/api/jobs/{job_id}", base_url=BASE).get_json()
        assert wait_for(lambda: get()["status"] in ("done", "error"), timeout=180)
        job = get()
        assert job["status"] == "done", job["error"]
        segs = job["segments"]
        assert " ".join(s["text"] for s in segs).strip()
        starts = [s["start"] for s in segs]
        assert starts == sorted(starts)
        assert all(s["end"] >= s["start"] for s in segs)
    finally:
        services(app).jobs.stop()
