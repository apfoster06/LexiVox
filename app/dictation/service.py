"""Hotkey dictation: press to start recording, press again to transcribe and copy.

The recording is converted and transcribed with the same engine, model and language
as file jobs, copied to the clipboard, then deleted. Transcript text is never logged.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from .. import audio
from ..backends.base import Cancelled, TranscribeOptions
from ..config import SettingsStore
from ..engine import Engine, ModelMissing
from ..events import EventBus
from .hotkey import HotkeyError, HotkeyListener, parse_hotkey
from .recorder import MicRecorder, RecorderError

log = logging.getLogger(__name__)

MIN_SECONDS = 0.4


def _beep(freq: int) -> None:
    try:
        import winsound

        threading.Thread(target=winsound.Beep, args=(freq, 90), daemon=True).start()
    except Exception:
        pass


class DictationService:
    def __init__(
        self,
        engine: Engine,
        settings: SettingsStore,
        bus: EventBus,
        temp_dir: Path,
        model_for: Callable[[], str],
        recorder: MicRecorder | None = None,
        copy: Callable[[str], None] | None = None,
        beeps: bool = True,
    ):
        self.engine = engine
        self.settings = settings
        self.bus = bus
        self.temp_dir = temp_dir
        self.model_for = model_for
        self.recorder = recorder or MicRecorder()
        if copy is None:
            from .clipboard import copy_text as copy
        self.copy = copy
        self.beeps = beeps
        self.state = "idle"  # idle | recording | transcribing
        self.error: str | None = None
        self.hotkey_error: str | None = None
        self.history: deque[dict] = deque(maxlen=20)
        self.listeners: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._hotkeys: HotkeyListener | None = None

    # ---- hotkey -------------------------------------------------------------------

    def start_hotkey(self) -> None:
        self._hotkeys = HotkeyListener(self.toggle)
        self._hotkeys.start()
        combo = self.settings.get().hotkey
        if combo:
            try:
                self._hotkeys.set(combo)
                self.hotkey_error = None
            except HotkeyError as e:
                self.hotkey_error = str(e)
                log.warning("hotkey not registered: %s", e)
        self._publish()

    def set_hotkey(self, combo: str) -> str:
        normalized = parse_hotkey(combo)[2] if combo else ""
        if self._hotkeys is not None:
            self._hotkeys.set(normalized)
        self.settings.update(hotkey=normalized)
        self.hotkey_error = None
        self._publish()
        return normalized

    def stop(self) -> None:
        if self._hotkeys is not None:
            self._hotkeys.stop()
        self.cancel()

    # ---- recording ----------------------------------------------------------------

    def toggle(self) -> None:
        with self._lock:
            if self.state == "idle":
                self._start()
            elif self.state == "recording":
                self.state = "transcribing"
                threading.Thread(target=self._finish, name="dictation", daemon=True).start()
                self._publish()
            # while transcribing, extra presses are ignored

    def cancel(self) -> None:
        with self._lock:
            if self.state == "recording":
                self.recorder.cancel()
                self.state = "idle"
            elif self.state == "transcribing":
                self._cancel.set()
        self._publish()

    def _start(self) -> None:
        self.error = None
        self._cancel = threading.Event()
        try:
            self.recorder.start()
        except RecorderError as e:
            self.error = str(e)
            self._publish()
            return
        self.state = "recording"
        if self.beeps:
            _beep(880)
        self._publish()
        threading.Thread(target=self._monitor, name="dictation-monitor", daemon=True).start()

    def _monitor(self) -> None:
        """Push level/elapsed updates to the UI and auto-stop at the length limit."""
        while self.state == "recording":
            if self.recorder.seconds() >= self.recorder.max_seconds:
                self.toggle()
                return
            self._publish(light=True)
            time.sleep(0.25)

    def _finish(self) -> None:
        raw = self.temp_dir / f"{uuid.uuid4().hex}-mic.wav"
        wav = self.temp_dir / f"{uuid.uuid4().hex}-mic16k.wav"
        if self.beeps:
            _beep(520)
        try:
            seconds = self.recorder.stop(raw)
            if seconds < MIN_SECONDS:
                raise _Soft("Recording was too short.")
            audio.to_wav16k(raw, wav, self._cancel)
            s = self.settings.get()
            opts = TranscribeOptions(
                language=None if s.language == "auto" else s.language, translate=s.translate
            )
            segments, choice, _ = self.engine.transcribe(
                wav, self.model_for(), opts, lambda seg: None, lambda notice: None, self._cancel
            )
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
            if not text:
                raise _Soft("No speech was heard.")
            self.copy(text)
            self.history.appendleft({"text": text, "seconds": round(seconds, 1), "created": time.time()})
            log.info("dictation: %.1fs transcribed on %s and copied", seconds, choice.key)
        except Cancelled:
            pass
        except (_Soft, ModelMissing, RecorderError, audio.AudioError) as e:
            self.error = str(e) if not self._cancel.is_set() else None
        except Exception as e:
            log.exception("dictation failed")
            self.error = f"Dictation failed: {type(e).__name__}. See the log folder."
        finally:
            for p in (raw, wav):
                p.unlink(missing_ok=True)
            with self._lock:
                self.state = "idle"
            self._publish()

    # ---- state --------------------------------------------------------------------

    def snapshot(self, light: bool = False) -> dict:
        snap = {
            "state": self.state,
            "seconds": round(self.recorder.seconds(), 1) if self.state == "recording" else 0,
            "level": round(self.recorder.level, 3),
        }
        if not light:
            snap.update(
                hotkey=self.settings.get().hotkey,
                hotkey_error=self.hotkey_error,
                error=self.error,
                history=list(self.history)[:10],
            )
        return snap

    def _publish(self, light: bool = False) -> None:
        snap = self.snapshot(light)
        self.bus.publish("dictation_level" if light else "dictation", snap)
        if not light:
            for fn in self.listeners:
                try:
                    fn(snap)
                except Exception:
                    log.exception("dictation listener failed")


class _Soft(Exception):
    """A user-facing, non-error outcome (too short, silence)."""
