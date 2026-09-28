import ctypes
import os
import threading

import pytest
from conftest import BASE, HDR, install_fake_model, needs_ffmpeg, wait_for

from app import services
from app.dictation.hotkey import HotkeyError, HotkeyListener, ModifierChord, parse_hotkey
from app.dictation.recorder import RecorderError

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows API")


def _post(client, path, json=None):
    return client.post(path, base_url=BASE, headers=HDR, json=json)


# ---- hotkey parsing ---------------------------------------------------------------

@pytest.mark.parametrize("combo,expected", [
    ("Ctrl+Alt+Space", "Ctrl+Alt+Space"),
    ("alt+ctrl+d", "Ctrl+Alt+D"),
    ("Win+Shift+F9", "Shift+Win+F9"),
    ("Ctrl+Shift+1", "Ctrl+Shift+1"),
    ("Ctrl+PageUp", "Ctrl+PageUp"),
])
def test_parse_hotkey_normalizes(combo, expected):
    assert parse_hotkey(combo)[2] == expected


@pytest.mark.parametrize("combo", ["Space", "Shift+A", "Ctrl+", "Hyper+A", "Ctrl+Alt+Ö", "Ctrl+F25"])
def test_parse_hotkey_rejects(combo):
    with pytest.raises(HotkeyError):
        parse_hotkey(combo)


def test_modifier_only_chords():
    assert parse_hotkey("win+ctrl") == (0x2 | 0x8, 0, "Ctrl+Win")
    assert parse_hotkey("Ctrl+Alt")[2] == "Ctrl+Alt"
    for bad in ("Alt+Shift", "Ctrl+Shift", "Ctrl+Ctrl"):
        with pytest.raises(HotkeyError):
            parse_hotkey(bad)


CTRL, LWIN, D, SHIFT = 0xA2, 0x5B, 0x44, 0xA0


def _chord(pressed=None):
    fired, masked = [], []
    chord = ModifierChord(0x2 | 0x8, lambda: fired.append(1), lambda: masked.append(1), pressed)
    return chord, fired, masked


def test_chord_fires_once_on_release():
    chord, fired, masked = _chord()
    for vk, down in [(CTRL, True), (LWIN, True), (LWIN, True), (LWIN, False), (CTRL, False)]:
        chord.on_key(vk, down)
    assert fired == [1]
    assert masked == [1]  # Start menu suppressed once


def test_chord_ignores_ctrl_win_plus_another_key():
    chord, fired, _ = _chord()
    for vk, down in [(CTRL, True), (LWIN, True), (D, True), (D, False), (LWIN, False), (CTRL, False)]:
        chord.on_key(vk, down)
    assert fired == []


def test_chord_ignores_extra_modifier_and_single_modifier():
    chord, fired, _ = _chord()
    for vk, down in [(CTRL, True), (LWIN, True), (SHIFT, True), (SHIFT, False), (LWIN, False), (CTRL, False)]:
        chord.on_key(vk, down)
    for vk, down in [(CTRL, True), (CTRL, False), (LWIN, True), (LWIN, False)]:
        chord.on_key(vk, down)
    assert fired == []


def test_chord_recovers_from_missed_key_up():
    held = {CTRL}
    chord, fired, _ = _chord(lambda vk: vk in held)
    chord.on_key(LWIN, True)  # Win down, then its key-up is lost (e.g. Win+L)
    chord.on_key(CTRL, True)  # stale Win is pruned, so Ctrl alone doesn't arm
    chord.on_key(CTRL, False)
    assert fired == []
    held.add(LWIN)
    for vk, down in [(CTRL, True), (LWIN, True), (LWIN, False), (CTRL, False)]:
        chord.on_key(vk, down)
    assert fired == [1]


def test_parse_hotkey_codes():
    mods, vk, _ = parse_hotkey("Ctrl+Alt+Space")
    assert (mods, vk) == (0x2 | 0x1, 0x20)
    assert parse_hotkey("Ctrl+F1")[1] == 0x70
    assert parse_hotkey("Ctrl+F24")[1] == 0x87


# ---- dictation flow ---------------------------------------------------------------

@needs_ffmpeg
def test_toggle_records_transcribes_and_copies(app, authed, fake_recorder, clipboard, sine_wav):
    install_fake_model(services(app).models, "faster-whisper", "base")
    services(app).settings.update(model="base")
    fake_recorder.source = sine_wav
    assert _post(authed, "/api/dictation/toggle").get_json()["dictation"]["state"] == "recording"
    assert _post(authed, "/api/dictation/toggle").get_json()["dictation"]["state"] == "transcribing"
    dictation = services(app).dictation
    assert wait_for(lambda: dictation.state == "idle")
    assert clipboard == ["hello world"]
    assert dictation.history[0]["text"] == "hello world"
    assert dictation.error is None
    assert list(services(app).paths.temp.iterdir()) == []
    state = authed.get("/api/state", base_url=BASE).get_json()["dictation"]
    assert state["history"][0]["text"] == "hello world"


@needs_ffmpeg
def test_auto_paste_follows_setting(app, authed, fake_recorder, clipboard, pastes, sine_wav):
    install_fake_model(services(app).models, "faster-whisper", "base")
    services(app).settings.update(model="base")
    fake_recorder.source = sine_wav
    dictation = services(app).dictation

    def dictate():
        _post(authed, "/api/dictation/toggle")
        _post(authed, "/api/dictation/toggle")
        assert wait_for(lambda: dictation.state == "idle")

    dictate()
    assert pastes == [1] and dictation.history[0]["pasted"] is True
    assert _post(authed, "/api/settings", {"auto_paste": False}).status_code == 200
    dictate()
    assert pastes == [1] and dictation.history[0]["pasted"] is False
    assert clipboard == ["hello world", "hello world"]  # always copied either way
    assert _post(authed, "/api/settings", {"auto_paste": "yes"}).status_code == 400


@needs_ffmpeg
def test_copy_again(app, authed, fake_recorder, clipboard, sine_wav):
    assert _post(authed, "/api/dictation/copy").status_code == 404
    install_fake_model(services(app).models, "faster-whisper", "base")
    services(app).settings.update(model="base")
    fake_recorder.source = sine_wav
    _post(authed, "/api/dictation/toggle")
    _post(authed, "/api/dictation/toggle")
    assert wait_for(lambda: services(app).dictation.state == "idle" and clipboard)
    assert _post(authed, "/api/dictation/copy").status_code == 200
    assert clipboard == ["hello world", "hello world"]


def test_too_short_recording_is_not_transcribed(app, authed, fake_recorder, clipboard, fake_backend):
    fake_recorder.duration = 0.1
    _post(authed, "/api/dictation/toggle")
    _post(authed, "/api/dictation/toggle")
    dictation = services(app).dictation
    assert wait_for(lambda: dictation.state == "idle")
    assert "too short" in dictation.error
    assert clipboard == [] and fake_backend.calls == []


@needs_ffmpeg
def test_missing_model_reported(app, authed, fake_recorder, clipboard, sine_wav):
    services(app).settings.update(model="large-v3-turbo")
    fake_recorder.source = sine_wav
    _post(authed, "/api/dictation/toggle")
    _post(authed, "/api/dictation/toggle")
    dictation = services(app).dictation
    assert wait_for(lambda: dictation.state == "idle")
    assert "not installed" in dictation.error
    assert clipboard == []


def test_microphone_failure_reported(app, authed, fake_recorder):
    fake_recorder.fail = RecorderError("Could not open the microphone.")
    snap = _post(authed, "/api/dictation/toggle").get_json()["dictation"]
    assert snap["state"] == "idle"
    assert "microphone" in snap["error"]


def test_cancel_discards_recording(app, authed, clipboard):
    _post(authed, "/api/dictation/toggle")
    assert _post(authed, "/api/dictation/cancel").get_json()["dictation"]["state"] == "idle"
    assert clipboard == []


def test_hotkey_endpoint_validates_and_saves(app, authed):
    assert _post(authed, "/api/dictation/hotkey", {"hotkey": "Shift+A"}).status_code == 409
    resp = _post(authed, "/api/dictation/hotkey", {"hotkey": "alt+ctrl+k"})
    assert resp.get_json()["hotkey"] == "Ctrl+Alt+K"
    assert services(app).settings.get().hotkey == "Ctrl+Alt+K"
    assert _post(authed, "/api/dictation/hotkey", {"hotkey": ""}).get_json()["hotkey"] == ""


def test_hotkey_endpoint_needs_csrf_header(authed):
    resp = authed.post("/api/dictation/toggle", base_url=BASE)
    assert resp.status_code == 403


# ---- real Windows APIs ------------------------------------------------------------

@windows_only
def test_real_hotkey_fires_on_keypress():
    pressed = threading.Event()
    listener = HotkeyListener(pressed.set)
    listener.start()
    try:
        listener.set("Ctrl+Alt+Shift+F24")  # obscure, so it won't clash with anything
        user32 = ctypes.windll.user32
        keys = [0x11, 0x12, 0x10, 0x87]  # Ctrl, Alt, Shift, F24
        for vk in keys:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(keys):
            user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        assert pressed.wait(3)
    finally:
        listener.set("")
        listener.stop()


@windows_only
def test_real_modifier_chord_via_keyboard_hook():
    pressed = threading.Event()
    listener = HotkeyListener(pressed.set)
    listener.start()
    try:
        listener.set("Ctrl+Alt")  # no Win key, so a failed test can't open the Start menu
        user32 = ctypes.windll.user32
        for vk in (0xA2, 0xA4):
            user32.keybd_event(vk, 0, 0, 0)
        for vk in (0xA4, 0xA2):
            user32.keybd_event(vk, 0, 2, 0)
        assert pressed.wait(3)
    finally:
        listener.set("")
        listener.stop()


@windows_only
def test_hotkey_already_taken_is_reported():
    a, b = HotkeyListener(lambda: None), HotkeyListener(lambda: None)
    a.start()
    b.start()
    try:
        a.set("Ctrl+Alt+Shift+F23")
        with pytest.raises(HotkeyError, match="already used"):
            b.set("Ctrl+Alt+Shift+F23")
    finally:
        a.set("")
        a.stop()
        b.stop()


@windows_only
def test_clipboard_round_trip_restores_original():
    from app.dictation.clipboard import copy_text, read_text

    original = read_text()
    try:
        copy_text("LexiVox test ✓ ünïcode")
        assert read_text() == "LexiVox test ✓ ünïcode"
    finally:
        copy_text(original)
