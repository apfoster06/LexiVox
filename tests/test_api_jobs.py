"""Upload -> job -> segments through the HTTP API, with a fake engine."""

import io

from conftest import BASE, HDR, install_fake_model, needs_ffmpeg, wait_for

from app import services


def _upload(client, files, **form):
    data = {"files": files, **form}
    return client.post("/api/jobs", base_url=BASE, headers=HDR, data=data, content_type="multipart/form-data")


@needs_ffmpeg
def test_upload_transcribes_and_cleans_temp(app, authed, sine_wav):
    install_fake_model(services(app).models, "faster-whisper", "tiny")
    resp = _upload(authed, [(sine_wav.open("rb"), "My Meeting.wav")], model="tiny")
    assert resp.status_code == 201, resp.get_json()
    job = resp.get_json()["jobs"][0]
    assert job["name"] == "My_Meeting.wav"
    assert wait_for(lambda: authed.get(f"/api/jobs/{job['id']}", base_url=BASE).get_json()["status"] == "done")
    detail = authed.get(f"/api/jobs/{job['id']}", base_url=BASE).get_json()
    assert [s["text"] for s in detail["segments"]] == ["hello", "world"]
    assert detail["engine"]["backend"] == "faster-whisper"
    assert list(services(app).paths.temp.iterdir()) == []


@needs_ffmpeg
def test_saved_upload_uses_random_name(app, authed, sine_wav, fake_backend):
    install_fake_model(services(app).models, "faster-whisper", "tiny")
    seen = []
    original = fake_backend.transcribe

    def spy(audio_path, *a, **k):
        seen.append(audio_path)
        return original(audio_path, *a, **k)

    fake_backend.transcribe = spy
    resp = _upload(authed, [(sine_wav.open("rb"), "..\\..\\evil name.wav")], model="tiny")
    assert resp.status_code == 201
    job = resp.get_json()["jobs"][0]
    assert ".." not in job["name"] and "\\" not in job["name"]
    assert wait_for(lambda: seen)
    assert seen[0].parent == services(app).paths.temp
    assert "evil" not in seen[0].name


def test_rejects_unsupported_extension(authed):
    resp = _upload(authed, [(io.BytesIO(b"MZ..."), "tool.exe")])
    assert resp.status_code == 400
    assert resp.get_json()["rejected"][0]["error"] == "Unsupported file type"


@needs_ffmpeg
def test_rejects_non_audio_with_audio_extension(app, authed):
    resp = _upload(authed, [(io.BytesIO(b"this is not audio at all"), "fake.mp3")])
    assert resp.status_code == 400
    assert resp.get_json()["rejected"]
    assert list(services(app).paths.temp.iterdir()) == []


def test_upload_limit(app, authed):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    resp = _upload(authed, [(io.BytesIO(b"x" * 4096), "big.wav")])
    assert resp.status_code == 413


def test_unknown_model_and_language_rejected(authed):
    assert _upload(authed, [(io.BytesIO(b"x"), "a.wav")], model="../../x").status_code == 400
    assert _upload(authed, [(io.BytesIO(b"x"), "a.wav")], language="klingon").status_code == 400


@needs_ffmpeg
def test_missing_model_gives_clear_error(authed, sine_wav):
    resp = _upload(authed, [(sine_wav.open("rb"), "a.wav")], model="tiny")
    job = resp.get_json()["jobs"][0]
    assert wait_for(lambda: authed.get(f"/api/jobs/{job['id']}", base_url=BASE).get_json()["status"] == "error")
    detail = authed.get(f"/api/jobs/{job['id']}", base_url=BASE).get_json()
    assert "not installed" in detail["error"]


def test_state_endpoint(authed):
    s = authed.get("/api/state", base_url=BASE).get_json()
    assert s["choice"]["backend"] == "faster-whisper"
    assert any(m["name"] == "tiny" for m in s["models"])
    assert s["languages"]["en"] == "English"


def test_settings_validation(authed):
    post = lambda body: authed.post("/api/settings", base_url=BASE, headers=HDR, json=body)
    assert post({"model": "tiny", "language": "de", "translate": True}).status_code == 200
    assert post({"model": "nope"}).status_code == 400
    assert post({"translate": "yes"}).status_code == 400
    s = authed.get("/api/state", base_url=BASE).get_json()["settings"]
    assert (s["model"], s["language"], s["translate"]) == ("tiny", "de", True)
