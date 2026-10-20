"""Security: CSRF, origin check, headers, hashed sessions, password hashes, throttling, body limit."""
import main
from conftest import PW, Client, make_client


def _raw_login(name="alice"):
    c = Client(main.app)
    r = c.post("/api/login", json={"name": name, "password": PW})
    assert r.status_code == 200
    return c, r.json()["csrf"]


def test_unsafe_call_without_csrf_is_403(alice):
    token = alice.csrf
    alice.csrf = ""
    r = alice.post("/api/me/onboarded")
    assert r.status_code == 403
    alice.csrf = "wrong"
    assert alice.post("/api/me/onboarded").status_code == 403
    alice.csrf = token
    assert alice.post("/api/me/onboarded").status_code == 200


def test_get_needs_no_csrf(alice):
    alice.csrf = ""
    assert alice.get("/api/me").status_code == 200


def test_csrf_token_is_per_session(alice):
    a1, t1 = _raw_login()
    a2, t2 = _raw_login()
    assert t1 != t2
    a1.csrf = t2
    assert a1.post("/api/me/onboarded").status_code == 403


def test_csrf_token_not_in_cookie(alice):
    assert alice.csrf and alice.csrf not in alice.cookies.get(main.SESSION_COOKIE, "")


def test_cross_origin_post_rejected(alice):
    r = alice.post("/api/me/onboarded", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = alice.post("/api/me/onboarded", headers={"Referer": "https://evil.example/a"})
    assert r.status_code == 403
    r = alice.post("/api/me/onboarded", headers={"Origin": "null"})
    assert r.status_code == 403


def test_same_origin_post_allowed(alice):
    r = alice.post("/api/me/onboarded", headers={"Origin": "http://testserver"})
    assert r.status_code != 403


def test_login_also_checks_origin(anon):
    main.db.create_user("Chef", PW)
    r = anon.post("/api/login", json={"name": "Chef", "password": PW}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_security_headers_everywhere(alice):
    for path in ("/api/me", "/"):
        h = alice.get(path).headers
        assert "default-src 'self'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"]
    assert alice.get("/api/me").headers["cache-control"] == "no-store"


def test_hsts_only_over_https(alice):
    assert "strict-transport-security" not in alice.get("/api/me").headers
    r = alice.get("/api/me", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in r.headers  # without TRUST_PROXY the header is ignored


def test_cookie_flags(anon):
    main.db.create_user("Chef", PW)
    c = anon.post("/api/login", json={"name": "Chef", "password": PW}).headers["set-cookie"].lower()
    assert "httponly" in c and "samesite=lax" in c and "secure" not in c


def test_cookie_secure_behind_trusted_proxy(anon, monkeypatch):
    main.db.create_user("Chef", PW)
    monkeypatch.setattr(main, "TRUST_PROXY", True)
    r = anon.post("/api/login", json={"name": "Chef", "password": PW}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()
    assert "strict-transport-security" in r.headers


def test_cookie_secure_forced(anon, monkeypatch):
    main.db.create_user("Chef", PW)
    monkeypatch.setattr(main, "COOKIE_SECURE", "true")
    r = anon.post("/api/login", json={"name": "Chef", "password": PW})
    assert "secure" in r.headers["set-cookie"].lower()


def test_session_tokens_are_stored_hashed(anon):
    main.db.create_user("Chef", PW)
    anon.post("/api/login", json={"name": "Chef", "password": PW})
    token = anon.cookies.get(main.SESSION_COOKIE)
    with main.db.db() as c:
        stored = [r[0] for r in c.execute("SELECT token FROM sessions")]
    assert token not in stored and main.db._sid(token) in stored


def test_password_hash_format_and_rehash():
    main.db.create_user("Chef", PW)
    u = main.db.find_user("Chef")
    h = main.db.hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert main.db.verify_password(PW, h) and not main.db.verify_password("anders-1234", h)
    # old, weaker hash (3 fields) is accepted and replaced at login
    import hashlib, os
    salt = os.urandom(16)
    legacy = f"scrypt${salt.hex()}${hashlib.scrypt(PW.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"
    assert main.db.verify_password(PW, legacy) and main.db.needs_rehash(legacy)
    with main.db.db() as c:
        c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (legacy, u["id"]))
    c = Client(main.app)
    assert c.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200
    with main.db.db() as c2:
        new = c2.execute("SELECT pw_hash FROM users WHERE id = ?", (u["id"],)).fetchone()[0]
    assert new != legacy and not main.db.needs_rehash(new)


def test_login_throttle_per_name_and_per_ip(anon):
    main.db.create_user("Chef", PW)
    for _ in range(main.LOGIN_MAX_FAILS):
        assert anon.post("/api/login", json={"name": "Chef", "password": "falsch-falsch"}).status_code == 401
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429
    main._login_fails.clear()
    for i in range(main.LOGIN_MAX_FAILS_IP):
        anon.post("/api/login", json={"name": f"x{i}", "password": "falsch-falsch"})
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429


def test_login_error_does_not_reveal_user(anon):
    main.db.create_user("Chef", PW)
    a = anon.post("/api/login", json={"name": "Chef", "password": "falsch-falsch"})
    b = anon.post("/api/login", json={"name": "Niemand", "password": "falsch-falsch"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_body_limit_413(alice):
    r = alice.post("/api/searches", content=b"x" * (main.MAX_BODY + 10), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_version_endpoint_needs_login_and_is_minimal(anon, alice):
    assert anon.get("/api/version").status_code == 401
    r = alice.get("/api/version")
    assert r.status_code == 200 and set(r.json()) == {"version", "commit"}


def test_status_admin_only(alice):
    bob = make_client("bob")
    assert alice.get("/api/status").status_code == 200
    assert bob.get("/api/status").status_code == 403


def test_non_admin_cannot_use_admin_api(alice):
    bob = make_client("bob")
    assert bob.get("/api/admin/users").status_code == 403
    assert bob.post("/api/admin/users", json={"name": "x", "password": PW}).status_code == 403
    assert bob.delete("/api/admin/users/1").status_code == 403


def test_password_change_kills_other_sessions(alice):
    a, _ = _raw_login()
    b, _ = _raw_login()
    assert a.put("/api/me/password", json={"old": PW, "new": "ganz-neu-1234"}).status_code == 200
    assert b.get("/api/me").status_code == 401


# ---------- Hardening: body limit without Content-Length, error details, session lifetime ----------

def _chunks(total_mb: float, size_mb: float = 1.0):
    for _ in range(int(total_mb / size_mb)):
        yield b"x" * int(size_mb * 1024 * 1024)


def test_the_body_limit_also_holds_without_a_content_length(anon, alice):
    """A chunked request has no Content-Length: the limit must count what really arrives (unauthenticated endpoints included)."""
    r = anon.post("/api/login", content=_chunks(3), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "Request too large"
    r = alice.post("/api/playlists", content=_chunks(3), headers={"content-type": "application/json"})
    assert r.status_code == 413
    ok = anon.post("/api/login", content=iter([b'{"name": "Alice", ', b'"password": "wrong-1234"}']), headers={"content-type": "application/json"})
    assert ok.status_code == 401  # small chunked bodies are fine


def test_uploads_keep_their_own_limit(alice):
    big = b"x" * (3 * 1024 * 1024)  # more than a JSON body may be, far less than an upload
    r = alice.post("/api/admin/restore", content=big, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 400  # not a valid backup (but it was read: the upload limit applies, not the JSON one)


def test_unexpected_errors_do_not_reveal_details(monkeypatch):
    import library
    from fastapi.testclient import TestClient

    def boom(*a, **k):
        raise RuntimeError("secret detail /srv/internal/path.db")

    monkeypatch.setattr(library, "search_tracks", boom)
    c = make_client("Alice")
    quiet = TestClient(main.app, raise_server_exceptions=False, cookies=c.cookies, headers={"X-CSRF-Token": c.csrf})
    r = quiet.get("/api/search", params={"q": "a"})
    assert r.status_code == 500 and "reference" in r.json()["detail"]
    assert "secret detail" not in r.text and "RuntimeError" not in r.text and "/srv/internal" not in r.text


def test_a_session_ends_at_its_maximum_age_even_if_it_is_used(alice, monkeypatch):
    assert alice.get("/api/me").status_code == 200
    with main.db.db() as c:
        c.execute("UPDATE sessions SET created_at = created_at - ?", (main.SESSION_MAX_AGE + 10,))  # the login is older than the maximum
    assert alice.get("/api/me").status_code == 401
    assert main.SESSION_MAX_AGE == 180 * 86400  # the default; SESSION_MAX_DAYS changes it, 0 turns the limit off


def test_the_maximum_age_can_be_turned_off(alice, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_AGE", 0)
    with main.db.db() as c:
        c.execute("UPDATE sessions SET created_at = created_at - ?", (400 * 86400,))
    assert alice.get("/api/me").status_code == 200  # only the inactivity limit counts then
