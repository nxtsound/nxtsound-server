import main
from conftest import PW


# ---------- First start and login ----------

def test_setup_creates_admin_once(anon):
    assert anon.get("/api/auth-state").json() == {"setup": True, "claim": None}
    assert anon.post("/api/setup", json={"name": "Chef", "password": "kurz"}).status_code == 400  # zu kurz
    r = anon.post("/api/setup", json={"name": "Chef", "password": PW})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
    assert anon.get("/api/me").json()["user"]["name"] == "Chef"  # direkt eingeloggt
    assert anon.get("/api/auth-state").json()["setup"] is False
    assert anon.post("/api/setup", json={"name": "Zweiter", "password": PW}).status_code == 403


def test_login_needs_correct_password(anon):
    main.db.create_user("Chef", PW)
    assert anon.post("/api/login", json={"name": "Chef", "password": "falsch-falsch"}).status_code == 401
    assert anon.post("/api/login", json={"name": "Chef", "password": ""}).status_code == 401
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200


def test_login_is_throttled(anon):
    main.db.create_user("Chef", PW)
    for _ in range(main.LOGIN_MAX_FAILS):
        assert anon.post("/api/login", json={"name": "Chef", "password": "nope-nope"}).status_code == 401
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429  # even the right one is blocked


def test_password_is_stored_hashed():
    main.db.create_user("Chef", PW)
    with main.db.db() as c:
        h = c.execute("SELECT pw_hash FROM users WHERE name = 'Chef'").fetchone()[0]
    assert h.startswith("scrypt$") and PW not in h


def test_old_database_admin_claims_with_first_login(anon):
    """Old database without passwords: the oldest user becomes admin and sets their password at the first login."""
    with main.db.db() as c:
        c.execute("INSERT INTO users(name, created_at) VALUES('Alt', 1)")
        c.execute("INSERT INTO users(name, created_at) VALUES('Alt2', 2)")
    main.db.init_db()
    assert anon.get("/api/auth-state").json() == {"setup": False, "claim": "Alt"}
    assert anon.post("/api/login", json={"name": "Alt2", "password": PW}).status_code == 401  # blocked without a password
    assert anon.post("/api/login", json={"name": "Alt", "password": "kurz"}).status_code == 400
    r = anon.post("/api/login", json={"name": "Alt", "password": PW})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True
    assert anon.post("/api/login", json={"name": "Alt", "password": "ganz-anders"}).status_code == 401  # jetzt gesetzt


def test_change_own_password_ends_sessions(alice):
    assert alice.put("/api/me/password", json={"old": "falsch-falsch", "new": "neues-pw-123"}).status_code == 403
    assert alice.put("/api/me/password", json={"old": PW, "new": "neues-pw-123"}).status_code == 200
    assert alice.get("/api/me").status_code == 401
    assert main.db.check_login("Alice", "neues-pw-123")["name"] == "Alice"
    assert main.db.check_login("Alice", PW) is None


# ---------- Admin ----------

def test_admin_routes_only_for_admin(alice, bob):  # alice is the first user and therefore admin
    assert alice.get("/api/admin/users").status_code == 200
    assert bob.get("/api/admin/users").status_code == 403
    assert bob.post("/api/admin/users", json={"name": "Neu", "password": PW}).status_code == 403


def test_admin_adds_edits_and_deletes_users(alice, anon):
    r = alice.post("/api/admin/users", json={"name": "Neu", "password": "passwort-neu"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is False
    assert alice.post("/api/admin/users", json={"name": "neu", "password": PW}).status_code == 409
    assert alice.post("/api/admin/users", json={"name": "X", "password": PW}).status_code == 400
    assert alice.post("/api/admin/users", json={"name": "Kurz", "password": "kurz"}).status_code == 422
    uid = r.json()["user"]["id"]
    assert anon.post("/api/login", json={"name": "Neu", "password": "passwort-neu"}).status_code == 200
    assert alice.patch(f"/api/admin/users/{uid}", json={"password": "anderes-pw-1"}).status_code == 200
    assert anon.get("/api/me").status_code == 401  # a password change ends old sessions
    users = {u["name"]: u for u in alice.get("/api/admin/users").json()["users"]}
    assert "quota_gb" not in users["Neu"] and "pw_hash" not in str(users["Neu"]).replace("'pw_hash': None", "")
    assert alice.delete(f"/api/admin/users/{uid}").status_code == 200
    assert "Neu" not in {u["name"] for u in alice.get("/api/admin/users").json()["users"]}
    assert alice.delete(f"/api/admin/users/{uid}").status_code == 404


def test_admin_cannot_remove_self(alice):
    me = alice.get("/api/me").json()["user"]
    assert alice.delete(f"/api/admin/users/{me['id']}").status_code == 400
    assert alice.patch(f"/api/admin/users/{me['id']}", json={"is_admin": False}).status_code == 400


# ---------- Sessions (30 Tage, gleitend) ----------

def _age_session(token: str, seconds: int):
    with main.db.db() as c:
        c.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (main.db._now() - seconds, main.db._sid(token)))


def test_session_cookie_lasts_30_days(anon):
    main.db.create_user("Chef", PW)
    r = anon.post("/api/login", json={"name": "Chef", "password": PW})
    assert f"Max-Age={30 * 86400}" in r.headers["set-cookie"]


def test_session_expires_after_inactivity_and_use_extends_it(anon):
    main.db.create_user("Chef", PW)
    anon.post("/api/login", json={"name": "Chef", "password": PW})
    token = anon.cookies.get(main.SESSION_COOKIE)
    _age_session(token, 29 * 86400)  # almost expired, but still valid
    r = anon.get("/api/me")
    assert r.status_code == 200
    assert "Max-Age" in r.headers.get("set-cookie", "")  # the cookie is extended
    with main.db.db() as c:
        assert main.db._now() - c.execute("SELECT last_seen FROM sessions WHERE token = ?", (main.db._sid(token),)).fetchone()[0] < 5  # Nutzung verlaengert
    _age_session(token, 29 * 86400)
    assert anon.get("/api/me").status_code == 200  # after the extension there are 29 days again
    _age_session(token, 31 * 86400)
    assert anon.get("/api/me").status_code == 401  # laenger als 30 Tage unbenutzt
    with main.db.db() as c:
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE token = ?", (main.db._sid(token),)).fetchone()[0] == 0


def test_active_session_does_not_write_every_request(anon):
    main.db.create_user("Chef", PW)
    anon.post("/api/login", json={"name": "Chef", "password": PW})
    r = anon.get("/api/me")
    assert "set-cookie" not in r.headers  # fresh session: no unnecessary re-setting


def test_purge_removes_only_expired_sessions():
    u = main.db.create_user("Chef", PW)
    old, new = main.db.create_session(u["id"])[0], main.db.create_session(u["id"])[0]
    _age_session(old, 40 * 86400)
    assert main.db.purge_sessions(main.SESSION_TTL) == 1
    assert main.db.user_for_token(new, main.SESSION_TTL)["name"] == "Chef"


# ---------- Admin per Umgebungsvariable (Docker) ----------

def test_admin_bootstrap_from_env(monkeypatch, anon):
    monkeypatch.setenv("ADMIN_USER", "Jane")
    monkeypatch.setenv("ADMIN_PASSWORD", "docker-pw-123")
    main._bootstrap_admin()
    assert main.db.find_user("Jane")["is_admin"] is True
    assert anon.post("/api/login", json={"name": "Jane", "password": "docker-pw-123"}).status_code == 200
    assert anon.get("/api/auth-state").json()["setup"] is False


def test_admin_bootstrap_only_on_empty_db_and_validates(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "Neu")
    monkeypatch.setenv("ADMIN_PASSWORD", "kurz")
    main._bootstrap_admin()
    assert main.db.count_users() == 0  # password too short: nothing created
    monkeypatch.setenv("ADMIN_PASSWORD", "langes-passwort")
    main.db.create_user("Vorhanden", PW)
    main._bootstrap_admin()
    assert main.db.find_user("Neu") is None  # DB not empty: ignored


