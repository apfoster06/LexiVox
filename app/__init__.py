"""Flask app factory."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field

from flask import Flask

from .backends import installed_backends
from .backends.base import Backend
from .backends.hardware import cpu_cores
from .config import Paths, SettingsStore
from .dictation.service import DictationService
from .engine import Engine, effective_model
from .events import EventBus
from .jobs import JobManager
from .models.downloads import DownloadService
from .models.manager import ModelManager
from .security import LaunchGuard, init_security

log = logging.getLogger(__name__)


@dataclass
class Services:
    paths: Paths
    settings: SettingsStore
    guard: LaunchGuard
    bus: EventBus
    models: ModelManager
    downloads: DownloadService
    engine: Engine
    jobs: JobManager
    dictation: DictationService
    last_heartbeat: float = field(default_factory=time.monotonic)


def clean_temp(paths: Paths) -> None:
    for p in paths.temp.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            log.warning("could not remove a stale temp file")


def create_app(
    paths: Paths,
    settings: SettingsStore,
    launch_token: str,
    *,
    port: int = 0,
    backends: dict[str, Backend] | None = None,
    models: ModelManager | None = None,
    start_worker: bool = True,
    recorder=None,
    copy=None,
    paste=None,
    polisher=None,
) -> Flask:
    paths.ensure()
    clean_temp(paths)

    app = Flask(__name__)
    app.config.update(
        PORT=port,
        MAX_CONTENT_LENGTH=settings.get().max_upload_mb * 1024 * 1024,
        JSON_SORT_KEYS=False,
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )

    bus = EventBus()
    models = models or ModelManager(paths.models)
    if backends is None:
        backends = installed_backends(cache_dir=paths.root / "ov-cache")
    engine = Engine(backends, models, settings)
    jobs = JobManager(engine, bus, paths.temp)
    guard = LaunchGuard(launch_token)
    dictation = DictationService(
        engine, settings, bus, paths.temp, lambda: effective_model(engine, cpu_cores()),
        recorder=recorder, copy=copy, paste=paste, polisher=polisher,
    )
    services = Services(
        paths, settings, guard, bus, models, DownloadService(models, bus), engine, jobs, dictation
    )
    app.extensions["lexivox"] = services

    init_security(app, guard)

    from .routes.api import api
    from .routes.pages import pages
    from .routes.settings_api import settings_api

    app.register_blueprint(pages)
    app.register_blueprint(api)
    app.register_blueprint(settings_api)

    if start_worker:
        jobs.start()
        # Probe devices (imports the engines, slow) and load the model before the window
        # or the first dictation needs them.
        def warm_up() -> None:
            engine.choices()
            engine.preload(effective_model(engine, cpu_cores()))

        threading.Thread(target=warm_up, name="engine-warmup", daemon=True).start()
    return app


def services(app: Flask) -> Services:
    return app.extensions["lexivox"]
