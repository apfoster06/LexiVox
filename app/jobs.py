"""Batch job queue: one background worker, one file at a time, clean cancellation."""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import audio
from .backends.base import Cancelled, Segment, TranscribeOptions
from .engine import Engine, ModelMissing
from .events import EventBus

log = logging.getLogger(__name__)

ACTIVE = ("converting", "transcribing")
FINISHED = ("done", "error", "cancelled")


@dataclass
class Job:
    id: str
    name: str  # display name only (already passed through secure_filename)
    src: Path  # server-generated path in the temp dir
    model: str
    opts: TranscribeOptions
    duration: float = 0.0
    status: str = "queued"
    progress: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    error: str | None = None
    notice: str | None = None
    engine: dict | None = None
    created: float = field(default_factory=time.time)
    elapsed: float | None = None
    transcript_id: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "model": self.model, "status": self.status,
            "progress": round(self.progress, 4), "duration": self.duration, "error": self.error,
            "notice": self.notice, "engine": self.engine, "created": self.created,
            "elapsed": self.elapsed, "segment_count": len(self.segments),
            "transcript_id": self.transcript_id,
            "language": self.opts.language, "translate": self.opts.translate,
        }

    def detail(self) -> dict:
        return {**self.summary(), "segments": [s.to_dict() for s in self.segments]}


class JobManager:
    def __init__(
        self,
        engine: Engine,
        bus: EventBus,
        temp_dir: Path,
        on_done: Callable[[Job], str | None] | None = None,
    ):
        self.engine = engine
        self.bus = bus
        self.temp_dir = temp_dir
        self.on_done = on_done
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="job-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
        self._queue.put(None)

    def submit(self, src: Path, name: str, model: str, opts: TranscribeOptions, duration: float) -> Job:
        job = Job(uuid.uuid4().hex, name, src, model, opts, duration)
        with self._lock:
            self._jobs[job.id] = job
        self._emit(job)
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in FINISHED:
            return False
        job.cancel.set()
        if job.status == "queued":
            self._finish(job, "cancelled")
        return True

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in FINISHED:
                return False
            del self._jobs[job_id]
        self.bus.publish("job_removed", {"id": job_id})
        return True

    def _emit(self, job: Job) -> None:
        self.bus.publish("job", job.summary())

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        job.status = status
        job.error = error
        if status == "done":
            job.progress = 1.0
        self._emit(job)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            job = self.get(job_id)
            if job is None or job.status != "queued":
                continue
            try:
                self._run(job)
            except Exception:
                log.exception("job worker crashed on a job")
                if job.status not in FINISHED:
                    self._finish(job, "error", "Unexpected error. See the log folder.")

    def _run(self, job: Job) -> None:
        wav = self.temp_dir / f"{job.id}.wav"
        started = time.monotonic()
        try:
            job.status = "converting"
            self._emit(job)
            audio.to_wav16k(job.src, wav, job.cancel)
            job.status = "transcribing"
            self._emit(job)

            def on_segment(seg: Segment) -> None:
                job.segments.append(seg)
                if job.duration > 0:
                    job.progress = min(0.999, seg.end / job.duration)
                self.bus.publish("segment", {"id": job.id, "progress": job.progress, "segment": seg.to_dict()})

            def on_restart(notice: str) -> None:
                job.segments.clear()
                job.progress = 0.0
                job.notice = notice
                self.bus.publish("job_reset", {"id": job.id})
                self._emit(job)

            _, choice, notice = self.engine.transcribe(
                wav, job.model, job.opts, on_segment, on_restart, job.cancel
            )
            job.engine = choice.to_dict()
            job.notice = notice
            job.elapsed = time.monotonic() - started
            if self.on_done:
                try:
                    job.transcript_id = self.on_done(job)
                except Exception:
                    log.exception("saving transcript failed")
            self._finish(job, "done")
        except (Cancelled, audio.AudioError) as e:
            if job.cancel.is_set():
                self._finish(job, "cancelled")
            else:
                self._finish(job, "error", str(e))
        except ModelMissing as e:
            self._finish(job, "error", str(e))
        except Exception as e:
            log.exception("transcription failed")
            self._finish(job, "error", f"Transcription failed: {type(e).__name__}. See the log folder.")
        finally:
            for p in (wav, job.src):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    log.warning("could not delete temp file")
