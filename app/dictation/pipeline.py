"""Transcribe a live recording in chunks while the user is still talking.

The monitor thread calls poll() a few times a second; whenever the chunker finds a
pause, that stretch of audio is queued. One worker thread transcribes queued chunks
in order (and, for the "clean up" polish style, cleans each one with the local AI).
When the user stops, only the last chunk is left to do.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import numpy as np

from .. import ollama
from .chunker import find_cut

log = logging.getLogger(__name__)

MIN_CHUNK_S = 0.3  # ignore slivers of audio shorter than this

TranscribeChunk = Callable[[np.ndarray, int, str], str]  # (samples, rate, context) -> text


class ChunkPipeline:
    def __init__(
        self,
        recorder,
        transcribe_chunk: TranscribeChunk,
        polish_chunk: Callable[[str], str] | None,
        cancel: threading.Event,
        on_progress: Callable[[], None] = lambda: None,
    ):
        self.recorder = recorder
        self.transcribe_chunk = transcribe_chunk
        self.polish_chunk = polish_chunk
        self.cancel = cancel
        self.on_progress = on_progress
        self.texts: list[str] = []
        self.polished: list[str] = []
        self.polish_error: Exception | None = None
        self.error: BaseException | None = None
        self.chunks = 0
        self._cut = 0
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[int, int] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="dictation-chunks", daemon=True)
        self._thread.start()

    @property
    def text(self) -> str:
        return " ".join(t for t in self.texts if t)

    @property
    def fully_polished(self) -> bool:
        return (
            self.polish_chunk is not None and self.polish_error is None
            and len(self.polished) == len(self.texts) and bool(self.texts)
        )

    def poll(self) -> None:
        """Queue the next finished stretch of speech, if there is one."""
        with self._lock:
            total = self.recorder.total()
            pending = self.recorder.read(self._cut, total)
            cut = find_cut(pending, self.recorder.rate)
            if cut:
                self._queue.put((self._cut, self._cut + cut))
                self._cut += cut

    def finish(self, timeout: float | None = None) -> None:
        """Queue whatever audio is left and wait for all chunks to be done."""
        with self._lock:
            self._queue.put((self._cut, self.recorder.total()))
            self._cut = self.recorder.total()
            self._queue.put(None)
        self._thread.join(timeout)

    def abort(self) -> None:
        """The user cancelled: stop any work in progress."""
        self.cancel.set()
        self._queue.put(None)

    def discard(self) -> None:
        """Nothing worth transcribing (e.g. too short): let the worker exit."""
        self._queue.put(None)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self.error is not None or self.cancel.is_set():
                continue  # drain the queue without doing more work
            start, end = item
            samples = self.recorder.read(start, end)
            if len(samples) < MIN_CHUNK_S * self.recorder.rate:
                continue
            try:
                text = self.transcribe_chunk(samples, self.recorder.rate, self.text[-200:])
            except BaseException as e:  # reported by the service when the user stops
                self.error = e
                continue
            self.chunks += 1
            if not text:
                continue
            self.texts.append(text)
            if self.polish_chunk is not None and self.polish_error is None:
                try:
                    self.polished.append(self.polish_chunk(text))
                except ollama.OllamaError as e:
                    self.polish_error = e
            self.on_progress()
