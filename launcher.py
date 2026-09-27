"""Starts the local server and opens the app in its own window.

- waitress (not the Flask dev server) on 127.0.0.1 and a random free port
- a per-launch token; the window opens /?launch=<token> which becomes a session cookie
- pywebview (WebView2) window; the process exits when the window closes
- fallback: Edge in app mode with an isolated profile, plus a heartbeat watchdog
"""

from __future__ import annotations

import os

# Libraries must never fetch anything on their own. Our model downloader doesn't use
# huggingface_hub, so offline mode can stay on for the whole process.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

import argparse  # noqa: E402
import logging  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from waitress import create_server  # noqa: E402

from app import create_app, services  # noqa: E402
from app.config import APP_NAME, Paths, SettingsStore, data_dir  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

log = logging.getLogger("launcher")

HEARTBEAT_TIMEOUT = 30.0


def open_webview(url: str) -> bool:
    try:
        import webview
    except ImportError:
        log.warning("pywebview is not available")
        return False
    try:
        webview.create_window(APP_NAME, url, width=1200, height=800, min_size=(420, 480))
        # private_mode keeps no cookies/storage between launches.
        webview.start(private_mode=True)
        return True
    except Exception:
        log.exception("pywebview failed to start")
        return False


def find_edge() -> str | None:
    candidates = [shutil.which("msedge")]
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return next((c for c in candidates if c and Path(c).is_file()), None)


def open_edge(url: str, paths: Paths, app) -> bool:
    edge = find_edge()
    if not edge:
        log.error("Microsoft Edge was not found")
        return False
    profile = paths.root / "edge-profile"
    subprocess.Popen([
        edge, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
        "--no-default-browser-check", "--disable-sync",
    ])
    svc = services(app)
    svc.last_heartbeat = time.monotonic() + 30  # give the window time to load
    while time.monotonic() - svc.last_heartbeat < HEARTBEAT_TIMEOUT:
        time.sleep(2)
    log.info("no heartbeat for %ss; shutting down", HEARTBEAT_TIMEOUT)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--edge", action="store_true", help="use Edge app mode instead of pywebview")
    args = parser.parse_args()

    paths = Paths(data_dir())
    paths.ensure()
    settings = SettingsStore(paths.config_file)
    setup_logging(paths.logs, settings.get().log_retention_days)

    token = secrets.token_urlsafe(32)
    app = create_app(paths, settings, token)
    max_body = settings.get().max_upload_mb * 1024 * 1024 + 10 * 1024 * 1024
    server = create_server(
        app, host="127.0.0.1", port=0, threads=16, ident="", max_request_body_size=max_body,
    )
    port = server.effective_port
    app.config["PORT"] = port
    threading.Thread(target=server.run, name="waitress", daemon=True).start()
    log.info("%s listening on 127.0.0.1:%s", APP_NAME, port)

    url = f"http://127.0.0.1:{port}/?launch={token}"
    try:
        opened = False if args.edge else open_webview(url)
        if not opened and not open_edge(url, paths, app):
            log.error("could not open a window")
    finally:
        services(app).jobs.stop()
        server.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
