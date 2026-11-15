"""Hotkey dictation: press to start recording, press again to transcribe, copy and paste.

Two shortcuts share one recorder: "dictate" pastes the words as spoken, "polish"
first has the local Ollama model clean them up. The recording is converted and
transcribed with the same engine as file jobs, then deleted. Transcript text is
never logged.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from .. import audio, ollama
from ..backends.base import Cancelled, TranscribeOptions
from ..config import SettingsStore
from ..engine import Engine, ModelMissing
from ..events import EventBus
from ..textproc import remove_fillers, vocabulary_prompt
from .hotkey import HotkeyError, HotkeyListener, parse_hotkey
from .pipeline import ChunkPipeline
from .recorder import MicRecorder, RecorderError, write_wav

log = logging.getLogger(__name__)

MIN_SECONDS = 0.4
MODES = ("dictate", "polish")
HOTKEY_SETTING = {"dictate": "hotkey", "polish": "polish_hotkey"}


def _beep(freq: int) -> None:
    try:
        import winsound

        threading.Thread(target=winsound.Beep, args=(freq, 90), daemon=True).start()
    except Exception:
        pass


class _Soft(Exception):
    """A user-facing, non-crash outcome (too short, silence)."""


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
        paste: Callable[[], bool] | None = None,
        polisher: Callable[[str], str] | None = None,
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
        if paste is None:
            from .paste import paste
        self.copy = copy
        self.paste = paste
        self.polisher = polisher or self._ollama_polish
        self.beeps = beeps
        self.state = "idle"  # idle | recording | transcribing | polishing
        self.mode = "dictate"
        self.error: str | None = None
        self.hotkey_errors: dict[str, str] = {}
        self.history: deque[dict] = deque(maxlen=20)
        self.listeners: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._hotkeys: HotkeyListener | None = None
        self._pipeline: ChunkPipeline | None = None

    # ---- hotkeys ------------------------------------------------------------------

    def start_hotkeys(self) -> None:
        self._hotkeys = HotkeyListener({m: (lambda m=m: self.toggle(m)) for m in MODES})
        self._hotkeys.start()
        s = self.settings.get()
        for mode in MODES:
            combo = getattr(s, HOTKEY_SETTING[mode])
            if not combo:
                continue
            try:
                self._hotkeys.set(combo, mode)
            except HotkeyError as e:
                self.hotkey_errors[mode] = str(e)
                log.warning("%s hotkey not registered: %s", mode, e)
        self._publish()

    def set_hotkey(self, combo: str, mode: str = "dictate") -> str:
        if mode not in MODES:
            raise HotkeyError("Unknown shortcut")
        normalized = parse_hotkey(combo)[2] if combo else ""
        other = getattr(self.settings.get(), HOTKEY_SETTING[MODES[1 - MODES.index(mode)]])
        if normalized and other and parse_hotkey(other)[2] == normalized:
            raise HotkeyError(f"{normalized} is already used for the other dictation shortcut.")
        if self._hotkeys is not None:
            self._hotkeys.set(normalized, mode)
        self.settings.update(**{HOTKEY_SETTING[mode]: normalized})
        self.hotkey_errors.pop(mode, None)
        self._publish()
        return normalized

    def stop(self) -> None:
        if self._hotkeys is not None:
            self._hotkeys.stop()
        self.cancel()

    # ---- recording ----------------------------------------------------------------

    def toggle(self, mode: str = "dictate") -> None:
        """Either shortcut stops a running recording; the mode is set when it starts."""
        with self._lock:
            if self.state == "idle":
                self._start(mode if mode in MODES else "dictate")
            elif self.state == "recording":
                self.state = "transcribing"
                threading.Thread(target=self._finish, name="dictation", daemon=True).start()
                self._publish()
            # while transcribing or polishing, extra presses are ignored

    def cancel(self) -> None:
        with self._lock:
            if self.state == "recording":
                self.recorder.cancel()
                self.state = "idle"
            elif self.state in ("transcribing", "polishing"):
                self._cancel.set()
            if self._pipeline is not None:
                self._pipeline.abort()
        self._publish()

    def _sounds(self) -> bool:
        return self.beeps and self.settings.get().sounds

    def _start(self, mode: str) -> None:
        s = self.settings.get()
        self.error = None
        self.mode = mode
        self._cancel = threading.Event()
        self.recorder.max_seconds = max(1, s.max_record_minutes) * 60
        try:
            self.recorder.start(s.mic_device)
        except RecorderError as e:
            self.error = str(e)
            self._publish()
            return
        chunk_polish = self.polisher if mode == "polish" and s.polish_style == "clean" else None
        self._pipeline = ChunkPipeline(
            self.recorder, self._transcribe_chunk, chunk_polish, self._cancel, self._publish
        )
        self.state = "recording"
        if self._sounds():
            _beep(880)
        self._publish()
        threading.Thread(target=self._monitor, name="dictation-monitor", daemon=True).start()
        # Load the speech model (and the AI model) while the user is talking.
        threading.Thread(target=self.engine.preload, args=(self.model_for(),), daemon=True).start()
        if mode == "polish" and self.polisher == self._ollama_polish:
            threading.Thread(target=self._warm_ollama, name="ollama-warmup", daemon=True).start()

    def _warm_ollama(self) -> None:
        try:
            s = self.settings.get()
            model = s.ollama_model or ollama.pick_default_model(ollama.list_models(s.ollama_url))
            if model:
                ollama.warm_up(s.ollama_url, model)
        except ollama.OllamaError:
            pass  # reported when polishing, if it's still down

    def _monitor(self) -> None:
        """Cut finished speech into chunks, push level/elapsed updates to the UI, and
        auto-stop at the length limit."""
        pipeline = self._pipeline
        tick = 0
        while self.state == "recording":
            if self.recorder.seconds() >= self.recorder.max_seconds:
                self.toggle()
                return
            if pipeline is not None and tick % 3 == 0:  # chunking still checks 4 times a second
                pipeline.poll()
            tick += 1
            self._publish(light=True)  # 12 levels a second keep the live waveform fluid
            time.sleep(0.083)

    def _finish(self) -> None:
        pipeline = self._pipeline
        if self._sounds():
            _beep(520)
        try:
            seconds = self.recorder.stop()
            if seconds < MIN_SECONDS:
                pipeline.discard()
                raise _Soft("Recording was too short.")
            pipeline.finish()  # only the last chunk is left to transcribe
            if self._cancel.is_set():
                raise Cancelled()
            if pipeline.error is not None:
                raise pipeline.error
            text = pipeline.text
            if not text:
                raise _Soft("No speech was heard.")
            polished = False
            if self.mode == "polish":
                if pipeline.fully_polished:
                    text, polished = " ".join(pipeline.polished), True
                elif pipeline.polish_error is not None:
                    self.error = f"{pipeline.polish_error} Pasted the text as spoken."
                else:
                    text, polished = self._polish(text)
            self.copy(text)
            pasted = self._paste()
            self.history.appendleft({
                "text": text, "seconds": round(seconds, 1), "created": time.time(),
                "pasted": pasted, "polished": polished,
            })
            log.info(
                "dictation: %.1fs in %d chunks, polished=%s, pasted=%s",
                seconds, pipeline.chunks, polished, pasted,
            )
        except Cancelled:
            pass
        except (_Soft, ModelMissing, RecorderError, audio.AudioError) as e:
            self.error = str(e) if not self._cancel.is_set() else None
        except Exception as e:
            log.exception("dictation failed")
            self.error = f"Dictation failed: {type(e).__name__}. See the log folder."
        finally:
            self._pipeline = None
            with self._lock:
                self.state = "idle"
            self._publish()

    def _transcribe_chunk(self, samples, rate: int, context: str) -> str:
        """Transcribe one stretch of recorded audio. Runs on the pipeline's worker."""
        raw = self.temp_dir / f"{uuid.uuid4().hex}-mic.wav"
        wav = self.temp_dir / f"{uuid.uuid4().hex}-mic16k.wav"
        try:
            write_wav(raw, samples, rate)
            audio.to_wav16k(raw, wav, self._cancel)
            s = self.settings.get()
            prompt = " ".join(p for p in (vocabulary_prompt(s.vocabulary), context) if p) or None
            opts = TranscribeOptions(
                language=None if s.language == "auto" else s.language,
                translate=s.translate, prompt=prompt,
            )
            segments, _, _ = self.engine.transcribe(
                wav, self.model_for(), opts, lambda seg: None, lambda notice: None, self._cancel
            )
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
            return remove_fillers(text) if s.remove_fillers else text
        finally:
            raw.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

    def _polish(self, text: str) -> tuple[str, bool]:
        """Returns (text, polished). Falls back to the spoken text if Ollama fails."""
        with self._lock:
            self.state = "polishing"
        self._publish()
        try:
            result = self.polisher(text)
        except ollama.OllamaError as e:
            self.error = f"{e} Pasted the text as spoken."
            return text, False
        if self._cancel.is_set():
            raise Cancelled()
        return result, True

    def _ollama_polish(self, text: str) -> str:
        s = self.settings.get()
        model = s.ollama_model or ollama.pick_default_model(ollama.list_models(s.ollama_url))
        if not model:
            raise ollama.OllamaError("No Ollama chat model is installed.")
        return ollama.polish(s.ollama_url, model, s.polish_style, text)

    def _paste(self) -> bool:
        if not self.settings.get().auto_paste:
            return False
        try:
            return self.paste()
        except Exception:
            log.exception("auto-paste failed")
            return False

    # ---- state --------------------------------------------------------------------

    def snapshot(self, light: bool = False) -> dict:
        snap = {
            "state": self.state,
            "mode": self.mode,
            "seconds": round(self.recorder.seconds(), 1) if self.state == "recording" else 0,
            "level": round(self.recorder.level, 3),
        }
        if not light:
            s = self.settings.get()
            snap.update(
                hotkey=s.hotkey,
                polish_hotkey=s.polish_hotkey,
                hotkey_errors=dict(self.hotkey_errors),
                partial=self._pipeline.text if self._pipeline is not None else "",
                error=self.error,
                history=list(self.history)[:10],
            )
        return snap

    def _publish(self, light: bool = False) -> None:
        snap = self.snapshot(light)
        self.bus.publish("dictation_level" if light else "dictation", snap)
        for fn in self.listeners:
            try:
                fn(snap)
            except Exception:
                log.exception("dictation listener failed")
