from conftest import BASE, HDR, PORT, TOKEN

from app.security import COOKIE_NAME


def test_no_cookie_is_401(client):
    assert client.get("/", base_url=BASE).status_code == 401
    resp = client.get("/api/state", base_url=BASE)
    assert resp.status_code == 401
    assert "error" in resp.get_json()


def test_static_files_need_cookie_too(client):
    assert client.get("/static/js/app.js", base_url=BASE).status_code == 401


def test_wrong_launch_token_is_401(client):
    assert client.get("/?launch=nope", base_url=BASE).status_code == 401


def test_launch_exchange_sets_strict_httponly_cookie(client):
    resp = client.get("/?launch=" + TOKEN, base_url=BASE)
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/"
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(COOKIE_NAME + "=")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert TOKEN not in cookie  # the cookie is a different secret
    assert client.get("/", base_url=BASE).status_code == 200


def test_launch_token_is_single_use(app):
    first, second = app.test_client(), app.test_client()
    assert first.get("/?launch=" + TOKEN, base_url=BASE).status_code == 303
    assert second.get("/?launch=" + TOKEN, base_url=BASE).status_code == 401


def test_wrong_cookie_is_401(client):
    client.set_cookie(COOKIE_NAME, "forged", domain="127.0.0.1")
    assert client.get("/api/state", base_url=BASE).status_code == 401


def test_bad_host_is_403(authed):
    assert authed.get("/api/health", base_url=f"http://evil.example:{PORT}").status_code == 403
    assert authed.get("/api/health", base_url="http://127.0.0.1:9999").status_code == 403
    assert authed.get("/api/health", headers={"Host": f"rebind.attacker:{PORT}"}, base_url=BASE).status_code == 403


def test_localhost_host_is_allowed(app):
    c = app.test_client()
    base = f"http://localhost:{PORT}"
    assert c.get("/?launch=" + TOKEN, base_url=base).status_code == 303
    assert c.get("/api/health", base_url=base).status_code == 200


def test_post_without_custom_header_is_403(authed):
    assert authed.post("/api/heartbeat", base_url=BASE).status_code == 403
    assert authed.post("/api/heartbeat", base_url=BASE, headers=HDR).status_code == 200


def test_delete_without_custom_header_is_403(authed):
    assert authed.delete("/api/jobs/x", base_url=BASE).status_code == 403


def test_security_headers_on_every_response(client, authed):
    for resp in (client.get("/api/health", base_url="http://127.0.0.1:1"), authed.get("/", base_url=BASE)):
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_page_has_no_inline_script_or_cdn(authed):
    html = authed.get("/", base_url=BASE).get_data(as_text=True)
    assert "<script>" not in html
    assert "http://" not in html and "https://" not in html
    assert " style=" not in html
