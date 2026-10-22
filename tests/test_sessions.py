"""The device list: what a login records, naming, ending other logins, and the database upgrade."""
import devices
import main
from conftest import PW, Client, make_client

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
WINDOWS_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def _login(name="Tester", ua=WINDOWS_CHROME, headers=None):
    c = Client(main.app)
    r = c.post("/api/login", json={"name": name, "password": PW}, headers={"User-Agent": ua, **(headers or {})})
    assert r.status_code == 200
    c.headers["User-Agent"] = ua
    return c


def _sessions(c, headers=None):
    return c.get("/api/me/sessions", headers=headers).json()["sessions"]


def test_a_login_records_the_device(client):
    c = _login(ua=IPHONE)
    mine = [s for s in _sessions(c) if s["current"]]
    assert len(mine) == 1
    s = mine[0]
    assert (s["browser"], s["system"]) == ("Safari", "iPhone")
    assert s["ip"] == "testclient" and s["last_ip"] == "testclient"
    assert s["name"] == "" and abs(s["created_at"] - s["last_active"]) < 5
    assert "ua" not in s  # the raw header is not handed out


def test_the_current_login_comes_first_then_the_newest(client):
    old = _login(ua=IPHONE)
    new = _login()
    with main.db.db() as c:
        c.execute("UPDATE sessions SET active_at = active_at - 600 WHERE ua = ?", (IPHONE,))
    assert _sessions(new)[0]["current"] and _sessions(new)[0]["system"] == "Windows"
    assert _sessions(old)[0]["current"] and _sessions(old)[0]["system"] == "iPhone"  # from the other device it is the other way round
    rest = [s for s in _sessions(new) if not s["current"]]
    assert [s["last_active"] for s in rest] == sorted((s["last_active"] for s in rest), reverse=True)  # then the most recently used


def test_use_refreshes_activity_and_ip(client, monkeypatch):
    monkeypatch.setattr(main, "TRUST_PROXY", True)
    c = _login(headers={"X-Forwarded-For": "203.0.113.5"})
    assert _sessions(c)[0]["ip"] == "203.0.113.5"
    with main.db.db() as db_:
        db_.execute("UPDATE sessions SET active_at = active_at - 3600")
    s = _sessions(c, {"X-Forwarded-For": "198.51.100.7"})[0]  # asking for the list is a use of the session too
    assert s["ip"] == "203.0.113.5"  # where it was logged in stays
    assert s["last_ip"] == "198.51.100.7"
    assert main.db._now() - s["last_active"] < 5


def test_a_device_can_be_renamed_and_reset(client):
    c = _login()
    sid = _sessions(c)[0]["id"]
    assert c.patch(f"/api/me/sessions/{sid}", json={"name": "  Living room  "}).status_code == 200
    assert _sessions(c)[0]["name"] == "Living room"
    assert c.patch(f"/api/me/sessions/{sid}", json={"name": ""}).status_code == 200
    assert _sessions(c)[0]["name"] == ""
    assert c.patch(f"/api/me/sessions/{sid}", json={"name": "x" * 41}).status_code == 422


def test_another_device_can_be_logged_out(client):
    a, b = _login(), _login(ua=IPHONE)
    bs = next(s for s in _sessions(b) if s["current"])
    assert a.delete(f"/api/me/sessions/{bs['id']}").status_code == 200
    assert b.get("/api/me").status_code == 401  # the other device is out at once
    assert a.get("/api/me").status_code == 200 and all(s["id"] != bs["id"] for s in _sessions(a))


def test_the_device_in_use_is_not_ended_here(client):
    a = _login()
    own = _sessions(a)[0]["id"]
    r = a.delete(f"/api/me/sessions/{own}")
    assert r.status_code == 400
    assert a.get("/api/me").status_code == 200


def test_other_peoples_devices_are_out_of_reach(client):
    a = _login()
    bob = make_client("Bob")
    bobs = _sessions(bob)[0]["id"]
    assert a.delete(f"/api/me/sessions/{bobs}").status_code == 404
    assert a.patch(f"/api/me/sessions/{bobs}", json={"name": "mine now"}).status_code == 404
    assert bob.get("/api/me").status_code == 200 and _sessions(bob)[0]["name"] == ""
    assert a.delete("/api/me/sessions/999999").status_code == 404


def test_log_out_everywhere_else(client):
    a, b, c3 = _login(), _login(ua=IPHONE), _login()
    r = a.delete("/api/me/sessions")
    assert r.status_code == 200 and r.json()["ended"] >= 2
    assert a.get("/api/me").status_code == 200
    assert b.get("/api/me").status_code == 401 and c3.get("/api/me").status_code == 401
    assert len(_sessions(a)) == 1


def test_the_device_list_needs_a_login(anon):
    assert anon.get("/api/me/sessions").status_code == 401
    assert anon.delete("/api/me/sessions").status_code in (401, 403)


def test_devices_are_named_from_the_user_agent():
    assert devices.describe(IPHONE) == ("Safari", "iPhone")
    assert devices.describe(WINDOWS_CHROME) == ("Chrome", "Windows")
    assert devices.describe("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0") == ("Edge", "Windows")
    assert devices.describe("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0") == ("Firefox", "Linux")
    assert devices.describe("NxtSound-iOS/1.0 (iPhone)") == ("NxtSound-iOS", "iPhone")
    assert devices.describe("curl/8.0") == ("", "")
    assert devices.describe("") == ("", "")
    assert len(devices.short_agent("x" * 1000)) == 300 and devices.short_agent("a\nb\x00c") == "abc"


def test_old_databases_get_the_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "old.db")
    main.db.init_db()
    uid = main.db.create_user("Old", PW)["id"]
    main.db.create_session(uid)
    with main.db.db() as c:
        for col in ("device", "ua", "ip", "last_ip", "active_at"):
            c.execute(f"ALTER TABLE sessions DROP COLUMN {col}")  # the table as an earlier version had it
        c.execute("UPDATE sessions SET last_seen = 1234")
    main.db.init_db()
    with main.db.db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        assert {"device", "ua", "ip", "last_ip", "active_at"} <= cols
        row = c.execute("SELECT active_at, ip FROM sessions").fetchone()
    assert row["active_at"] == 1234 and row["ip"] == ""  # existing logins keep their last use, the IP is unknown
