"""Starts the local server and opens the app in its own window.

- waitress (not the Flask dev server) on 127.0.0.1 and a random free port
- a per-launch token; the window opens /?launch=<token> which becomes a session cookie
- pywebview (WebView2) window; closing it hides to the tray so the dictation hotkey
  keeps working. Quit from the tray menu.
- fallback: Edge in app mode with an isolated profile (heartbeat watchdog if no tray)
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
from app.overlay import RecordingOverlay  # noqa: E402
from app.tray import Tray  # noqa: E402
from app.winshell import (  # noqa: E402
    enable_dpi_awareness, register_app_id, set_window_icon, write_icon,
)

log = logging.getLogger("launcher")

HEARTBEAT_TIMEOUT = 30.0


def find_edge() -> str | None:
    candidates = [shutil.which("msedge")]
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return next((c for c in candidates if c and Path(c).is_file()), None)


class Launcher:
    def __init__(self, app, port: int, first_token: str, paths: Paths):
        self.app = app
        self.svc = services(app)
        self.port = port
        self.first_token = first_token
        self.paths = paths
        self.quit = threading.Event()
        self.window = None
        self.edge_mode = False
        self.tray = Tray(self.show, self.svc.dictation.toggle, self.request_quit)
        self.overlay = RecordingOverlay(lambda: self.svc.settings.get().overlay_position)
        self.icon_ico: Path | None = None
        self._told_about_tray = False
        self._last_error: str | None = None

    def url(self, token: str) -> str:
        return f"http://127.0.0.1:{self.port}/?launch={token}"

    # ---- tray actions -------------------------------------------------------------

    def show(self) -> None:
        if self.window is not None:
            self.window.show()
            self.window.restore()
        elif self.edge_mode:
            self.open_edge(self.url(self.svc.guard.issue()))

    def request_quit(self) -> None:
        self.quit.set()
        if self.window is not None:
            self.window.destroy()

    def on_dictation(self, snap: dict) -> None:
        self.tray.update(snap)
        self.overlay.update(snap)
        if "error" not in snap:  # frequent level-only update
            return
        # Success is silent (the text is pasted or on the clipboard); only problems pop up.
        error = snap.get("error")
        if error and error != self._last_error:
            self.tray.notify(error)
        self._last_error = error

    # ---- windows ------------------------------------------------------------------

    def run_webview(self) -> bool:
        try:
            import webview
        except ImportError:
            log.warning("pywebview is not available")
            return False
        try:
            self.window = webview.create_window(
                APP_NAME, self.url(self.first_token), width=1200, height=800, min_size=(420, 480)
            )
            self.window.events.closing += self._on_closing
            self.window.events.shown += self._on_shown
            webview.start(private_mode=True)  # blocks until the window is destroyed
            return True
        except Exception:
            log.exception("pywebview failed to start")
            self.window = None
            return False

    def _on_shown(self) -> None:
        if self.icon_ico is not None:
            set_window_icon(APP_NAME, self.icon_ico)

    def _on_closing(self):
        if self.quit.is_set() or self.tray.icon is None:
            return True
        self.window.hide()
        if not self._told_about_tray:
            self._told_about_tray = True
            hotkey = self.svc.settings.get().hotkey
            self.tray.notify(
                f"Still running in the tray{f' — press {hotkey} to dictate' if hotkey else ''}. "
                "Right-click the icon to quit."
            )
        return False  # cancel the close

    def open_edge(self, url: str) -> bool:
        edge = find_edge()
        if not edge:
            log.error("Microsoft Edge was not found")
            return False
        subprocess.Popen([
            edge, f"--app={url}", f"--user-data-dir={self.paths.root / 'edge-profile'}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
        ])
        return True

    def run_edge(self) -> bool:
        self.edge_mode = True
        if not self.open_edge(self.url(self.first_token)):
            return False
        if self.tray.icon is not None:
            self.quit.wait()  # the tray's Quit ends the app
            return True
        self.svc.last_heartbeat = time.monotonic() + 30  # give the window time to load
        while time.monotonic() - self.svc.last_heartbeat < HEARTBEAT_TIMEOUT:
            time.sleep(2)
        log.info("no heartbeat for %ss; shutting down", HEARTBEAT_TIMEOUT)
        return True

    def run(self, force_edge: bool) -> None:
        try:
            self.icon_ico, icon_png = write_icon(self.paths.root / "icon")
            register_app_id(icon_png)
        except Exception:
            log.exception("could not set up the app icon")
        self.tray.start()
        self.overlay.start()
        self.svc.dictation.listeners.append(self.on_dictation)
        self.svc.dictation.start_hotkeys()
        self.tray.update(self.svc.dictation.snapshot())
        opened = False if force_edge else self.run_webview()
        if not opened and not self.quit.is_set() and not self.run_edge():
            log.error("could not open a window")


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--edge", action="store_true", help="use Edge app mode instead of pywebview")
    args = parser.parse_args()
    enable_dpi_awareness()  # before any window exists (Tk overlay, tray, WebView2)

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

    launcher = Launcher(app, port, token, paths)
    try:
        launcher.run(args.edge)
    finally:
        svc = services(app)
        svc.dictation.stop()
        svc.jobs.stop()
        launcher.tray.stop()
        launcher.overlay.stop()
        server.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
