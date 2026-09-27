"""Local-only, rotating, redacted logs with age-based retention.

Nothing here sends data anywhere. Transcript text must never be logged; the
redaction below is a safety net for paths and URLs that reach log messages.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_QUERY_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s?#\"']+)\?[^\s#\"']*")
# A path component with an extension, right after a separator. The extension must start
# with a letter so things like "1/2.5" aren't touched.
_FILE_RE = re.compile(
    r"(?<=[\\/])[^\\/\r\n\"'<>|:*?]+\.([A-Za-z][A-Za-z0-9]{0,4})(?=$|[\s\"'),;\]])"
)


def _home_patterns() -> list[re.Pattern]:
    home = str(Path.home())
    variants = {home, home.replace("\\", "/"), home.replace("/", "\\")}
    return [re.compile(re.escape(v), re.IGNORECASE) for v in sorted(variants, key=len, reverse=True)]


_HOME_RES = _home_patterns()


def redact(text: str) -> str:
    """Replace the home dir with ~, file names with <file>.<ext>, drop URL query strings."""
    for pattern in _HOME_RES:
        text = pattern.sub("~", text)
    text = _QUERY_RE.sub(r"\1?<redacted>", text)
    text = _FILE_RE.sub(lambda m: f"<file>.{m.group(1).lower()}", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def purge_old_logs(logs_dir: Path, retention_days: int, now: float | None = None) -> int:
    cutoff = (now or time.time()) - retention_days * 86400
    removed = 0
    for p in logs_dir.glob("*.log*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def setup_logging(logs_dir: Path, retention_days: int = 14, level: int = logging.INFO) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    purge_old_logs(logs_dir, max(1, retention_days))

    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = RotatingFileHandler(
        logs_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [handler]
    if sys.stderr is not None:  # None under pythonw
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)

    def _log_thread_exc(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("thread").error(
            "unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _log_thread_exc
