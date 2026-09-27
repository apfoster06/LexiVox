"""Runs one user-started model download at a time and reports progress on the event bus."""

from __future__ import annotations

import logging
import threading
import time

from ..backends.base import Cancelled
from ..events import EventBus
from .manager import ModelError, ModelManager

log = logging.getLogger(__name__)


class DownloadService:
    def __init__(self, models: ModelManager, bus: EventBus):
        self.models = models
        self.bus = bus
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._cancel = threading.Event()

    def state(self) -> dict | None:
        with self._lock:
            return dict(self._state) if self._state else None

    def start(self, backend: str, model: str) -> dict:
        variant = self.models.variant(backend, model)  # raises ModelError for unknown names
        with self._lock:
            if self._state and self._state["status"] in ("downloading", "verifying"):
                raise ModelError("A download is already running")
            self._cancel = threading.Event()
            self._state = {
                "backend": backend, "model": model, "status": "downloading",
                "done": 0, "total": variant.total_size, "error": None,
            }
            state = dict(self._state)
        threading.Thread(
            target=self._run, args=(backend, model, self._cancel), name="model-download", daemon=True
        ).start()
        self.bus.publish("download", state)
        return state

    def cancel(self) -> None:
        self._cancel.set()

    def _update(self, **changes) -> None:
        with self._lock:
            if self._state is None:
                return
            self._state.update(changes)
            state = dict(self._state)
        self.bus.publish("download", state)

    def _run(self, backend: str, model: str, cancel: threading.Event) -> None:
        last = 0.0

        def progress(done: int, total: int) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last >= 0.5 or done >= total:
                last = now
                self._update(done=done, total=total)

        log.info("model download started: %s/%s", backend, model)
        try:
            self.models.download(backend, model, progress, cancel)
            self._update(status="done")
        except Cancelled:
            self._update(status="cancelled")
        except Exception as e:
            log.warning("model download failed: %s", e)
            self._update(status="error", error=str(e) if isinstance(e, ModelError) else "Download failed. Check your connection.")
