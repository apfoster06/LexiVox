"""Security middleware for the local web app.

- Only Host headers for 127.0.0.1:<port> / localhost:<port> are accepted (DNS rebinding).
- The launcher opens /?launch=<token>; that single-use token is exchanged for an
  HttpOnly, SameSite=Strict session cookie. Every other request needs that cookie.
- State-changing requests must carry a custom header that cross-site forms can't set.
- Strict CSP and related headers on every response.
"""

from __future__ import annotations

import hmac
import secrets
import threading

from flask import Flask, Response, jsonify, redirect, request

COOKIE_NAME = "cvw_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "CroweVibeWhisper"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class LaunchGuard:
    def __init__(self, launch_token: str):
        self._launch_token = launch_token
        self._used = False
        self._lock = threading.Lock()
        # Separate from the launch token, so a leaked launch URL is useless after first use.
        self.session_secret = secrets.token_urlsafe(32)

    def exchange(self, candidate: str) -> bool:
        with self._lock:
            if self._used or not _equal(candidate, self._launch_token):
                return False
            self._used = True
            return True

    def valid_session(self, cookie: str | None) -> bool:
        return bool(cookie) and _equal(cookie, self.session_secret)


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _deny(status: int, message: str) -> Response:
    if request.path.startswith("/api/"):
        resp = jsonify(error=message)
    else:
        resp = Response(message, mimetype="text/plain")
    resp.status_code = status
    return resp


def init_security(app: Flask, guard: LaunchGuard) -> None:
    @app.before_request
    def _check_request():
        port = app.config["PORT"]
        host = (request.host or "").lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return _deny(403, "Forbidden host")

        has_session = guard.valid_session(request.cookies.get(COOKIE_NAME))
        if request.path == "/" and "launch" in request.args and request.method == "GET":
            if has_session:
                return redirect("/", code=303)
            if guard.exchange(request.args.get("launch", "")):
                resp = redirect("/", code=303)
                resp.set_cookie(
                    COOKIE_NAME, guard.session_secret,
                    httponly=True, samesite="Strict", path="/",
                )
                return resp
            return _deny(401, "Invalid or used launch token. Restart the app.")

        if not has_session:
            return _deny(401, "Not authorized. Open the app from its launcher.")
        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return _deny(403, "Missing request header")
        return None

    @app.after_request
    def _security_headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp
