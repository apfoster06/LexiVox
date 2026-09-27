"""Settings, dictation, microphone and Ollama endpoints."""

from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from .. import ollama, services
from ..dictation.hotkey import HotkeyError
from ..dictation.recorder import list_microphones
from ..languages import LANGUAGES

settings_api = Blueprint("settings_api", __name__, url_prefix="/api")

# Settings the page may change directly. Hotkeys go through /dictation/hotkey (they
# must be registered with Windows); internal fields (benchmark, …) aren't exposed.
EDITABLE = (
    "model", "language", "translate", "theme", "auto_paste", "remove_fillers", "vocabulary",
    "mic_device", "sounds", "max_record_minutes", "polish_style", "ollama_url", "ollama_model",
    "overlay_position",
)


def _svc():
    return services(current_app)


def _error(message: str, status: int = 400):
    resp = jsonify(error=message)
    resp.status_code = status
    return resp


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def public_settings(svc) -> dict:
    s = asdict(svc.settings.get())
    out = {k: s[k] for k in EDITABLE}
    out.update(hotkey=s["hotkey"], polish_hotkey=s["polish_hotkey"])
    return out


def _validate(svc, key: str, value):
    """Returns (clean value, None) or (None, error message)."""
    if key in ("translate", "auto_paste", "remove_fillers", "sounds"):
        return (value, None) if isinstance(value, bool) else (None, f"{key} must be true or false")
    if key == "model":
        ok = value == "" or value in svc.models.catalog
        return (value, None) if ok else (None, "Unknown model")
    if key == "language":
        ok = value == "auto" or value in LANGUAGES
        return (value, None) if ok else (None, "Unknown language")
    if key == "theme":
        return (value, None) if value in ("system", "light", "dark") else (None, "Unknown theme")
    if key == "overlay_position":
        ok = value in ("bottom", "right", "off")
        return (value, None) if ok else (None, "Unknown position")
    if key == "polish_style":
        return (value, None) if value in ollama.POLISH_STYLES else (None, "Unknown style")
    if key == "max_record_minutes":
        ok = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 60
        return (value, None) if ok else (None, "Max length must be 1 to 60 minutes")
    if key == "vocabulary":
        ok = isinstance(value, str) and len(value) <= 4000
        return (value, None) if ok else (None, "Vocabulary is too long")
    if key in ("mic_device", "ollama_model"):
        ok = isinstance(value, str) and len(value) <= 300
        return (value, None) if ok else (None, f"Invalid {key}")
    if key == "ollama_url":
        if not isinstance(value, str):
            return None, "Invalid URL"
        try:
            return ollama.validate_base_url(value), None
        except ollama.OllamaError as e:
            return None, str(e)
    return None, f"{key} can't be changed here"


@settings_api.post("/settings")
def update_settings():
    svc = _svc()
    changes = {}
    for key, value in _body().items():
        clean, error = _validate(svc, key, value)
        if error:
            return _error(error)
        changes[key] = clean
    svc.settings.update(**changes)
    if changes.keys() & {"auto_paste", "remove_fillers", "polish_style"}:
        svc.dictation._publish()
    return jsonify(settings=public_settings(svc))


@settings_api.get("/microphones")
def microphones():
    return jsonify(microphones=list_microphones(), selected=_svc().settings.get().mic_device)


@settings_api.get("/ollama/status")
def ollama_status():
    s = _svc().settings.get()
    try:
        models = ollama.list_models(s.ollama_url)
    except ollama.OllamaError as e:
        return jsonify(running=False, error=str(e), models=[], selected=s.ollama_model)
    chat = [m for m in models if "embed" not in m.lower()]
    return jsonify(
        running=True, models=chat, selected=s.ollama_model,
        default=ollama.pick_default_model(models),
        styles={k: v[0] for k, v in ollama.POLISH_STYLES.items()},
    )


# ---- dictation --------------------------------------------------------------------

@settings_api.post("/dictation/toggle")
def dictation_toggle():
    svc = _svc()
    mode = _body().get("mode", "dictate")
    svc.dictation.toggle(mode if mode in ("dictate", "polish") else "dictate")
    return jsonify(dictation=svc.dictation.snapshot())


@settings_api.post("/dictation/cancel")
def dictation_cancel():
    svc = _svc()
    svc.dictation.cancel()
    return jsonify(dictation=svc.dictation.snapshot())


@settings_api.post("/dictation/copy")
def dictation_copy():
    svc = _svc()
    index = _body().get("index", 0)
    history = list(svc.dictation.history)
    if not isinstance(index, int) or not 0 <= index < len(history):
        return _error("Nothing to copy", 404)
    svc.dictation.copy(history[index]["text"])
    return jsonify(ok=True)


@settings_api.post("/dictation/hotkey")
def dictation_hotkey():
    svc = _svc()
    body = _body()
    combo, mode = body.get("hotkey", ""), body.get("mode", "dictate")
    if not isinstance(combo, str) or len(combo) > 64 or mode not in ("dictate", "polish"):
        return _error("Invalid shortcut")
    try:
        normalized = svc.dictation.set_hotkey(combo, mode)
    except HotkeyError as e:
        return _error(str(e), 409)
    return jsonify(hotkey=normalized, mode=mode)
