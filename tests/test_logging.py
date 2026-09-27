import logging
import os
import time
from pathlib import Path

from app.logging_setup import RedactingFormatter, purge_old_logs, redact

HOME = str(Path.home())


def test_home_dir_replaced_with_tilde():
    assert redact(f"opened {HOME}\\Music") == "opened ~\\Music"
    assert redact(f"opened {HOME.replace(chr(92), '/')}/Music") == "opened ~/Music"
    assert redact(f"opened {HOME.upper()}\\x") == "opened ~\\x"


def test_file_names_redacted_keep_extension():
    assert redact(f"{HOME}\\Music\\meeting notes.M4A failed") == "~\\Music\\<file>.m4a failed"
    assert redact("D:/audio/secret-interview.mp3") == "D:/audio/<file>.mp3"
    assert redact("path='C:\\x\\a.wav'") == "path='C:\\x\\<file>.wav'"


def test_directories_and_numbers_untouched():
    assert redact("C:\\data\\models\\tiny") == "C:\\data\\models\\tiny"
    assert redact("ratio 1/2.5 done") == "ratio 1/2.5 done"


def test_url_query_string_stripped():
    out = redact("GET https://example.com/a?token=abc&x=1 ok")
    assert "token" not in out and "abc" not in out
    assert out.startswith("GET https://example.com/a?<redacted>")


def test_formatter_redacts_args_and_exceptions():
    fmt = RedactingFormatter("%(message)s")
    try:
        raise ValueError(f"cannot open {HOME}\\Desktop\\private.wav")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "file %s", (f"{HOME}\\x\\y.mp3",), sys.exc_info())
    out = fmt.format(record)
    assert HOME not in out
    assert "private" not in out and "y.mp3" not in out
    assert "<file>.wav" in out and "<file>.mp3" in out


def test_purge_old_logs(tmp_path):
    old = tmp_path / "app.log.3"
    new = tmp_path / "app.log"
    other = tmp_path / "keep.txt"
    for p in (old, new, other):
        p.write_text("x")
    past = time.time() - 20 * 86400
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    assert purge_old_logs(tmp_path, 14) == 1
    assert not old.exists() and new.exists() and other.exists()
