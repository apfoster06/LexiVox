"""In-process event bus feeding the Server-Sent Events stream."""

from __future__ import annotations

import json
import threading
from collections import deque


class EventBus:
    def __init__(self, maxlen: int = 5000):
        self._events: deque[tuple[int, str, dict]] = deque(maxlen=maxlen)
        self._seq = 0
        self._cond = threading.Condition()

    def publish(self, kind: str, data: dict) -> None:
        with self._cond:
            self._seq += 1
            self._events.append((self._seq, kind, data))
            self._cond.notify_all()

    @property
    def last_seq(self) -> int:
        with self._cond:
            return self._seq

    def wait(self, after: int, timeout: float) -> list[tuple[int, str, dict]] | None:
        """Events newer than `after`. None means the client fell too far behind and must resync."""
        with self._cond:
            if self._seq <= after:
                self._cond.wait(timeout)
            if self._seq <= after:
                return []
            if self._events and self._events[0][0] > after + 1:
                return None
            return [e for e in self._events if e[0] > after]


def sse_format(seq: int, kind: str, data: dict) -> str:
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data)}\n\n"
