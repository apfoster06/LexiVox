"""Filler removal, vocabulary, AI polish (Ollama), microphone and settings options."""

import http.server
import json
import threading

import pytest
from conftest import BASE, HDR, Segment, install_fake_model, needs_ffmpeg, wait_for

from app import ollama, services
from app.textproc import remove_fillers, vocabulary_prompt, vocabulary_terms


def _post(client, path, json_body=None):
    return client.post(path, base_url=BASE, headers=HDR, json=json_body)


# ---- text -------------------------------------------------------------------------

@pytest.mark.parametrize("raw,clean", [
    ("Um, so we need the report.", "So we need the report."),
    ("I think, um, we should go.", "I think we should go."),
    ("We will, uh, send it. Uh, then review.", "We will send it. Then review."),
    ("Hmm. Okay, umm let us start.", "Okay, let us start."),
    ("The umbrella and the hummus are here.", "The umbrella and the hummus are here."),
    ("Mm-hmm, sounds good.", "Mm-hmm, sounds good."),
    ("Uh-huh.", "Uh-huh."),
    ("Revenue was 1,200 dollars, uh, last year.", "Revenue was 1,200 dollars last year."),
    ("We need it by Friday um.", "We need it by Friday."),
    ("Um.", ""),
])
def test_remove_fillers(raw, clean):
    assert remove_fillers(raw) == clean


def test_vocabulary():
    assert vocabulary_terms("Crowe\nEBITDA, Jane Okafor;\n\ncrowe\n") == ["Crowe", "EBITDA", "Jane Okafor"]
    assert vocabulary_prompt("") is None
    assert vocabulary_prompt("Crowe\nEBITDA") == "Glossary: Crowe, EBITDA."
    assert len(vocabulary_prompt("\n".join(f"term{i}" for i in range(500)))) <= 600


# ---- Ollama -----------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434", "http://localhost:11434", "http://127.0.0.2:8080", "http://[::1]:11434",
])
def test_ollama_loopback_allowed(url):
    assert ollama.validate_base_url(url)


@pytest.mark.parametrize("url", [
    "http://192.168.1.5:11434", "http://example.com:11434", "https://127.0.0.1:11434",
    "file:///c:/x", "http://user:pw@127.0.0.1:11434", "http://127.0.0.1.evil.com/", "ftp://localhost",
])
def test_ollama_remote_rejected(url):
    with pytest.raises(ollama.OllamaError):
        ollama.validate_base_url(url)


@pytest.fixture
def fake_ollama():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen2.5:7b"}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(req)
            self._send({"response": "<think>hmm</think> Tidy text."})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def test_ollama_client(fake_ollama):
    url, seen = fake_ollama
    names = ollama.list_models(url)
    assert ollama.pick_default_model(names) == "qwen2.5:7b"
    assert ollama.polish(url, "qwen2.5:7b", "email", "send it friday") == "Tidy text."
    assert seen[0]["stream"] is False and seen[0]["think"] is False
    assert "email" in seen[0]["system"].lower()
    assert seen[0]["prompt"] == "send it friday"


def test_ollama_not_running():
    with pytest.raises(ollama.OllamaError, match="isn't running"):
        ollama.list_models("http://127.0.0.1:9")


def test_ollama_status_endpoint(app, authed, fake_ollama):
    url, _ = fake_ollama
    assert _post(authed, "/api/settings", {"ollama_url": url}).status_code == 200
    s = authed.get("/api/ollama/status", base_url=BASE).get_json()
    assert s["running"] and s["models"] == ["qwen2.5:7b"] and s["default"] == "qwen2.5:7b"
    assert _post(authed, "/api/settings", {"ollama_url": "http://10.0.0.5:11434"}).status_code == 400


# ---- dictation with options -------------------------------------------------------

def _dictate(app, authed, mode="dictate"):
    _post(authed, "/api/dictation/toggle", {"mode": mode})
    _post(authed, "/api/dictation/toggle", {"mode": mode})
    d = services(app).dictation
    assert wait_for(lambda: d.state == "idle")
    return d


@pytest.fixture
def ready(app, fake_recorder, sine_wav, fake_backend):
    install_fake_model(services(app).models, "faster-whisper", "base")
    services(app).settings.update(model="base")
    fake_recorder.source = sine_wav
    fake_backend.segments = [Segment(0, 1, "Um, send the"), Segment(1, 2, "report, uh, to Crow.")]
    return app


@needs_ffmpeg
def test_fillers_and_vocabulary_applied(ready, authed, clipboard, fake_backend, fake_recorder):
    _post(authed, "/api/settings", {"vocabulary": "Crowe\nEBITDA", "mic_device": "Headset Mic"})
    _dictate(ready, authed)
    assert clipboard == ["Send the report to Crow."]
    assert fake_backend.last_opts.prompt == "Glossary: Crowe, EBITDA."
    assert fake_recorder.device_name == "Headset Mic"

    _post(authed, "/api/settings", {"remove_fillers": False})
    _dictate(ready, authed)
    assert clipboard[-1] == "Um, send the report, uh, to Crow."


@needs_ffmpeg
def test_polish_mode(ready, authed, clipboard, polisher):
    d = _dictate(ready, authed, mode="polish")
    assert polisher.calls == ["Send the report to Crow."]
    assert clipboard == ["Polished: Send the report to Crow."]
    assert d.history[0]["polished"] is True and d.error is None


@needs_ffmpeg
def test_polish_falls_back_to_spoken_text(ready, authed, clipboard, polisher):
    polisher.fail = ollama.OllamaError("Ollama isn't running. Start it and try again.")
    d = _dictate(ready, authed, mode="polish")
    assert clipboard == ["Send the report to Crow."]
    assert d.history[0]["polished"] is False
    assert "Pasted the text as spoken" in d.error


@needs_ffmpeg
def test_plain_dictation_never_polishes(ready, authed, polisher):
    _dictate(ready, authed)
    assert polisher.calls == []


def test_max_length_setting_applied(app, authed, fake_recorder):
    _post(authed, "/api/settings", {"max_record_minutes": 2})
    _post(authed, "/api/dictation/toggle")
    assert fake_recorder.max_seconds == 120
    _post(authed, "/api/dictation/cancel")


# ---- settings + endpoints ---------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"max_record_minutes": 0}, {"max_record_minutes": 61}, {"max_record_minutes": True},
    {"sounds": "yes"}, {"polish_style": "poem"}, {"vocabulary": "x" * 5000},
    {"benchmark": {}}, {"hotkey": "Ctrl+Win"}, {"nope": 1},
])
def test_settings_rejects(authed, body):
    assert _post(authed, "/api/settings", body).status_code == 400


def test_settings_round_trip(authed):
    body = {"sounds": False, "max_record_minutes": 5, "polish_style": "bullets", "remove_fillers": False}
    assert _post(authed, "/api/settings", body).status_code == 200
    s = authed.get("/api/state", base_url=BASE).get_json()["settings"]
    assert {k: s[k] for k in body} == body


def test_two_shortcuts_cannot_match(app, authed):
    assert _post(authed, "/api/dictation/hotkey", {"mode": "dictate", "hotkey": "Ctrl+Win"}).status_code == 200
    resp = _post(authed, "/api/dictation/hotkey", {"mode": "polish", "hotkey": "win+ctrl"})
    assert resp.status_code == 409
    assert _post(authed, "/api/dictation/hotkey", {"mode": "polish", "hotkey": "Ctrl+Alt+Win"}).status_code == 200
    assert _post(authed, "/api/dictation/hotkey", {"mode": "bogus", "hotkey": "Ctrl+K"}).status_code == 400
    s = services(app).settings.get()
    assert (s.hotkey, s.polish_hotkey) == ("Ctrl+Win", "Ctrl+Alt+Win")


def test_microphones_endpoint(authed, monkeypatch):
    import app.routes.settings_api as sa

    monkeypatch.setattr(sa, "list_microphones", lambda: ["Mic A", "Mic B"])
    data = authed.get("/api/microphones", base_url=BASE).get_json()
    assert data["microphones"] == ["Mic A", "Mic B"]
