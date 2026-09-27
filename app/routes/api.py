"""JSON API and the Server-Sent Events stream."""

from __future__ import annotations

import logging
import time
import uuid

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.utils import secure_filename

from .. import audio, services
from ..backends.base import TranscribeOptions
from ..backends.hardware import cpu_cores, cpu_name
from ..config import APP_NAME
from ..dictation.hotkey import HotkeyError
from ..engine import effective_model as _effective_model
from ..engine import recommended_model
from ..events import sse_format
from ..languages import LANGUAGES
from ..models.manager import ModelError

log = logging.getLogger(__name__)
api = Blueprint("api", __name__, url_prefix="/api")


def _svc():
    return services(current_app)


def _error(message: str, status: int = 400):
    resp = jsonify(error=message)
    resp.status_code = status
    return resp


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def effective_model(svc) -> str:
    return _effective_model(svc.engine, cpu_cores())


def models_state(svc) -> list[dict]:
    out = []
    for name, entry in svc.models.catalog.items():
        out.append({
            "name": name,
            "label": entry.label,
            "variants": {
                b: {"installed": svc.models.is_installed(b, name), "size": v.total_size}
                for b, v in entry.variants.items()
            },
        })
    return out


@api.get("/health")
def health():
    return jsonify(ok=True)


@api.post("/heartbeat")
def heartbeat():
    _svc().last_heartbeat = time.monotonic()
    return jsonify(ok=True)


@api.get("/state")
def state():
    svc = _svc()
    s = svc.settings.get()
    choice = svc.engine.current()
    model = effective_model(svc)
    return jsonify(
        app={"name": APP_NAME},
        settings={"model": s.model, "language": s.language, "translate": s.translate, "theme": s.theme},
        effective_model=model,
        recommended_model=recommended_model(choice, cpu_cores()),
        choice=choice.to_dict() if choice else None,
        choices=[c.to_dict() for c in svc.engine.choices()],
        hardware={"cpu": cpu_name(), "cores": cpu_cores()},
        models=models_state(svc),
        download=svc.downloads.state(),
        jobs=[j.summary() for j in svc.jobs.list()],
        languages=LANGUAGES,
        dictation=svc.dictation.snapshot(),
    )


@api.post("/settings")
def update_settings():
    svc = _svc()
    body = _json_body()
    changes = {}
    if "model" in body:
        if body["model"] != "" and body["model"] not in svc.models.catalog:
            return _error("Unknown model")
        changes["model"] = body["model"]
    if "language" in body:
        if body["language"] != "auto" and body["language"] not in LANGUAGES:
            return _error("Unknown language")
        changes["language"] = body["language"]
    if "translate" in body:
        if not isinstance(body["translate"], bool):
            return _error("translate must be true or false")
        changes["translate"] = body["translate"]
    if "theme" in body:
        if body["theme"] not in ("system", "light", "dark"):
            return _error("Unknown theme")
        changes["theme"] = body["theme"]
    svc.settings.update(**changes)
    return jsonify(ok=True)


# ---- jobs -------------------------------------------------------------------------

@api.post("/jobs")
def create_jobs():
    svc = _svc()
    files = request.files.getlist("files")
    if not files:
        return _error("No files were uploaded")
    s = svc.settings.get()
    model = request.form.get("model") or effective_model(svc)
    if model not in svc.models.catalog:
        return _error("Unknown model")
    language = request.form.get("language", s.language)
    if language != "auto" and language not in LANGUAGES:
        return _error("Unknown language")
    translate = request.form.get("translate", str(s.translate)).lower() in ("1", "true", "on")
    opts = TranscribeOptions(language=None if language == "auto" else language, translate=translate)

    created, rejected = [], []
    for f in files:
        display = secure_filename(f.filename or "") or "upload"
        if not audio.extension_allowed(f.filename or ""):
            rejected.append({"name": display, "error": "Unsupported file type"})
            continue
        ext = (f.filename or "").rsplit(".", 1)[-1].lower()
        dst = svc.paths.temp / f"{uuid.uuid4().hex}.{ext}"
        f.save(dst)
        try:
            duration = audio.probe(dst)
        except audio.AudioError as e:
            dst.unlink(missing_ok=True)
            rejected.append({"name": display, "error": str(e)})
            continue
        created.append(svc.jobs.submit(dst, display, model, opts, duration).summary())
    status = 201 if created else 400
    return jsonify(jobs=created, rejected=rejected), status


@api.get("/jobs")
def list_jobs():
    return jsonify(jobs=[j.summary() for j in _svc().jobs.list()])


@api.get("/jobs/<job_id>")
def get_job(job_id: str):
    job = _svc().jobs.get(job_id)
    if job is None:
        return _error("Not found", 404)
    return jsonify(job.detail())


@api.post("/jobs/<job_id>/cancel")
def cancel_job(job_id: str):
    return jsonify(ok=_svc().jobs.cancel(job_id))


@api.delete("/jobs/<job_id>")
def remove_job(job_id: str):
    return jsonify(ok=_svc().jobs.remove(job_id))


# ---- dictation --------------------------------------------------------------------

@api.post("/dictation/toggle")
def dictation_toggle():
    svc = _svc()
    svc.dictation.toggle()
    return jsonify(dictation=svc.dictation.snapshot())


@api.post("/dictation/cancel")
def dictation_cancel():
    svc = _svc()
    svc.dictation.cancel()
    return jsonify(dictation=svc.dictation.snapshot())


@api.post("/dictation/copy")
def dictation_copy():
    svc = _svc()
    if not svc.dictation.history:
        return _error("Nothing to copy", 404)
    svc.dictation.copy(svc.dictation.history[0]["text"])
    return jsonify(ok=True)


@api.post("/dictation/hotkey")
def dictation_hotkey():
    svc = _svc()
    combo = _json_body().get("hotkey", "")
    if not isinstance(combo, str) or len(combo) > 64:
        return _error("Invalid shortcut")
    try:
        normalized = svc.dictation.set_hotkey(combo)
    except HotkeyError as e:
        return _error(str(e), 409)
    return jsonify(hotkey=normalized)


# ---- models -----------------------------------------------------------------------

@api.get("/models")
def list_models():
    svc = _svc()
    return jsonify(models=models_state(svc), download=svc.downloads.state())


@api.post("/models/download")
def download_model():
    svc = _svc()
    body = _json_body()
    try:
        state = svc.downloads.start(str(body.get("backend", "")), str(body.get("model", "")))
    except ModelError as e:
        return _error(str(e), 409 if "already" in str(e) else 400)
    return jsonify(download=state)


@api.post("/models/download/cancel")
def cancel_download():
    _svc().downloads.cancel()
    return jsonify(ok=True)


@api.post("/models/rescan")
def rescan_models():
    svc = _svc()
    result = svc.models.rescan()
    return jsonify(result=result, models=models_state(svc))


# ---- events -----------------------------------------------------------------------

@api.get("/events")
def events():
    bus = _svc().bus
    last_id = request.headers.get("Last-Event-ID", "")
    resume = last_id.isdigit()
    start = int(last_id) if resume else bus.last_seq

    def stream():
        after = start
        yield "retry: 2000\n\n"
        if not resume:
            yield sse_format(after, "sync", {})
        while True:
            batch = bus.wait(after, timeout=15)
            if batch is None:  # client fell behind; tell it to refetch state
                after = bus.last_seq
                yield sse_format(after, "sync", {})
                continue
            if not batch:
                yield ": ping\n\n"
                continue
            for seq, kind, data in batch:
                after = seq
                yield sse_format(seq, kind, data)

    return Response(
        stream(), mimetype="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
