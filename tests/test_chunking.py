"""Live chunking: cut points, the chunk pipeline, and per-chunk AI clean-up."""

import wave

import numpy as np
import pytest
from conftest import BASE, HDR, install_fake_model, needs_ffmpeg, wait_for

from app import ollama, services
from app.dictation.chunker import MAX_S, MIN_S, find_cut

RATE = 16000


def speechlike(seconds: float, pause_every: float = 3.0, pause: float = 0.5, seed: int = 0) -> np.ndarray:
    """Loud noise bursts ('words') separated by quiet gaps ('breaths')."""
    rng = np.random.default_rng(seed)
    out, t = [], 0.0
    while t < seconds:
        talk = min(pause_every, seconds - t)
        out.append((rng.standard_normal(int(talk * RATE)) * 4000).astype(np.int16))
        t += talk
        if t < seconds:
            gap = min(pause, seconds - t)
            out.append((rng.standard_normal(int(gap * RATE)) * 20).astype(np.int16))
            t += gap
    return np.concatenate(out)


def simulate(samples: np.ndarray, step: float = 0.25) -> list[int]:
    """Feed audio the way the monitor thread does; returns absolute cut positions."""
    cut, cuts, t = 0, [], 0
    while t < len(samples):
        t = min(len(samples), t + int(step * RATE))
        c = find_cut(samples[cut:t], RATE)
        if c:
            cut += c
            cuts.append(cut)
    return cuts


def is_quiet(samples: np.ndarray, at: int) -> bool:
    window = samples[max(0, at - 800):at + 800].astype(np.float32)
    return float(np.sqrt((window ** 2).mean())) < 200


def test_cuts_land_in_pauses_with_sane_lengths():
    audio = speechlike(60)
    cuts = simulate(audio)
    assert 3 <= len(cuts) <= 7
    bounds = [0, *cuts]
    for a, b in zip(bounds, bounds[1:]):
        assert MIN_S <= (b - a) / RATE <= MAX_S
    assert all(is_quiet(audio, c) for c in cuts)


def test_no_cut_before_minimum_or_in_short_audio():
    assert find_cut(speechlike(7), RATE) is None
    assert simulate(speechlike(7.9)) == []


def test_continuous_sound_is_cut_at_max_length():
    audio = (np.random.default_rng(1).standard_normal(RATE * 40) * 4000).astype(np.int16)
    cuts = simulate(audio)
    assert cuts and all((b - a) / RATE <= MAX_S + 0.5 for a, b in zip([0, *cuts], cuts))


def test_silence_threshold_adapts_to_noisy_room():
    rng = np.random.default_rng(2)
    audio = speechlike(40).astype(np.int32) + (rng.standard_normal(RATE * 40)[: len(speechlike(40))] * 300).astype(np.int32)
    cuts = simulate(np.clip(audio, -32768, 32767).astype(np.int16))
    assert cuts, "should still find the pauses above a steady background noise"


# ---- through the dictation service ------------------------------------------------

@pytest.fixture
def long_wav(tmp_path):
    path = tmp_path / "long.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(speechlike(40).tobytes())
    return path


@pytest.fixture
def ready(app, fake_recorder, long_wav):
    install_fake_model(services(app).models, "faster-whisper", "base")
    services(app).settings.update(model="base", remove_fillers=False)
    fake_recorder.source = long_wav
    return app


def _dictate(app, authed, mode="dictate", wait_s=1.5):
    authed.post("/api/dictation/toggle", base_url=BASE, headers=HDR, json={"mode": mode})
    d = services(app).dictation
    # Let the monitor thread cut chunks "while talking".
    assert wait_for(lambda: d._pipeline is not None and d._pipeline.chunks >= 2, timeout=10)
    authed.post("/api/dictation/toggle", base_url=BASE, headers=HDR, json={"mode": mode})
    assert wait_for(lambda: d.state == "idle", timeout=15)
    return d


@needs_ffmpeg
def test_chunks_are_transcribed_while_recording(ready, authed, clipboard, fake_backend):
    calls = []
    original = fake_backend.transcribe

    def spy(audio_path, opts, on_segment, cancel):
        calls.append(opts.prompt)
        return original(audio_path, opts, on_segment, cancel)

    fake_backend.transcribe = spy
    d = _dictate(ready, authed)
    assert d.error is None
    assert len(calls) >= 3  # several chunks plus the final one
    assert calls[0] is None  # no context for the first chunk
    assert "hello world" in calls[1]  # later chunks get the text so far as context
    assert clipboard[0].startswith("hello world hello world")
    assert list(services(ready).paths.temp.iterdir()) == []


@needs_ffmpeg
def test_clean_polish_runs_per_chunk(ready, authed, clipboard, polisher):
    services(ready).settings.update(polish_style="clean")
    d = _dictate(ready, authed, mode="polish")
    assert len(polisher.calls) >= 3 and all(c == "hello world" for c in polisher.calls)
    assert clipboard[0].startswith("Polished: hello world Polished: hello world")
    assert d.history[0]["polished"] is True


@needs_ffmpeg
def test_email_polish_runs_once_on_everything(ready, authed, clipboard, polisher):
    services(ready).settings.update(polish_style="email")
    _dictate(ready, authed, mode="polish")
    assert len(polisher.calls) == 1
    assert polisher.calls[0].count("hello world") >= 3


@needs_ffmpeg
def test_chunk_polish_failure_falls_back_to_spoken_text(ready, authed, clipboard, polisher):
    services(ready).settings.update(polish_style="clean")
    polisher.fail = ollama.OllamaError("Ollama isn't running.")
    d = _dictate(ready, authed, mode="polish")
    assert clipboard[0].startswith("hello world hello world")
    assert d.history[0]["polished"] is False
    assert "Pasted the text as spoken" in d.error
    assert len(polisher.calls) == 1  # stops trying after the first failure
