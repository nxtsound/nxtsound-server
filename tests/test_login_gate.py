import re

import main
from conftest import PW, ids_by_title

PUBLIC = {"/api/login", "/api/logout", "/api/setup", "/api/auth-state", "/api/server-info"}
INT_PARAMS = {"pid", "entry_id", "sid", "user_id"}


def test_every_api_route_requires_login(anon):
    """Scans all routes from the OpenAPI schema: without a cookie every route except login/logout must answer 401."""
    checked = 0
    for path_tpl, ops in main.app.openapi()["paths"].items():
        if not path_tpl.startswith("/api/") or path_tpl in PUBLIC:
            continue
        path = re.sub(r"\{(\w+)\}", lambda m: "1" if m.group(1) in INT_PARAMS else "test:00000001", path_tpl)
        for method in ops:
            kwargs = {"json": {}} if method in ("post", "patch", "put") else {}
            resp = anon.request(method.upper(), path, **kwargs)
            assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"
            checked += 1
    assert checked >= 25  # guard against an empty scan


def test_login_page_itself_is_public(anon):
    assert anon.get("/").status_code == 200
    assert anon.get("/app.js").status_code == 200


def test_stream_needs_login_and_supports_range(anon, alice, lib):
    tid = ids_by_title(alice)["Alpha"]
    assert anon.get(f"/api/stream/{tid}").status_code == 401
    r = alice.get(f"/api/stream/{tid}", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content[:4] == b"RIFF" and len(r.content) == 4
    assert r.headers["content-type"].startswith("audio/wav")
    full = alice.get(f"/api/stream/{tid}")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"


def test_unknown_track_is_404(alice, lib):
    assert alice.get("/api/stream/local:00000000-0000-0000-0000-000000000000").status_code == 404
    assert alice.get("/api/cover/local:00000000-0000-0000-0000-000000000000").status_code == 404


def test_cover_needs_login(anon, alice, lib):
    tid = ids_by_title(alice)["Alpha"]
    assert anon.get(f"/api/cover/{tid}").status_code == 401


def test_server_info_is_public(anon):
    r = anon.get("/api/server-info")
    assert r.status_code == 200
    j = r.json()
    assert j["server"] == "nxtsound" and j["name"] and j["version"] and j["api_version"] == main.API_VERSION
    assert "password" in j["login_methods"] and "library" in j["functions"]
    assert j["setup_required"] is True  # no user yet
    main.db.create_user("Erste", PW)
    assert anon.get("/api/server-info").json()["setup_required"] is False


def test_session_dies_with_logout(alice):
    assert alice.get("/api/searches").status_code == 200
    alice.post("/api/logout")
    assert alice.get("/api/searches").status_code == 401
    assert alice.get("/api/status").status_code == 401
