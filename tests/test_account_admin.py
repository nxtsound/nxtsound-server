import main
from conftest import PW, Client


# ---------- Check and change the name ----------

def test_name_check_states(alice, bob):
    chk = lambda n: alice.get("/api/name-check", params={"name": n}).json()
    assert chk("")["state"] == "empty"
    assert chk("x")["state"] == "invalid" and "characters" in chk("x")["message"]
    assert chk("<b>")["state"] == "invalid"
    assert chk("Alice")["state"] == "same"
    assert chk("alice")["state"] == "same" or chk("alice")["state"] == "free"  # same name, only different capitalisation
    assert chk("Neuer Name")["state"] == "free"
    r = chk("bob")  # case does not matter
    assert r["state"] == "taken" and r["suggestions"]
    assert all(s.lower() != "bob" and not main.db.name_taken(s) for s in r["suggestions"])


def test_name_check_needs_login(anon):
    assert anon.get("/api/name-check", params={"name": "abc"}).status_code == 401


def test_rename_unique_and_case_only_change(alice, bob):
    assert bob.put("/api/me/name", json={"name": "ALICE"}).status_code == 409
    assert bob.put("/api/me/name", json={"name": "x"}).status_code == 400
    assert bob.put("/api/me/name", json={"name": "Bobby"}).json() == {"name": "Bobby"}
    assert bob.get("/api/me").json()["user"]["name"] == "Bobby"  # Session bleibt gueltig
    assert main.db.find_user("Bob") is None and main.db.find_user("bobby") is not None
    assert bob.put("/api/me/name", json={"name": "bobby"}).status_code == 200  # capitalisation only
    # the old name is free again
    assert alice.get("/api/name-check", params={"name": "Bob"}).json()["state"] == "free"
    assert main.db.check_login("bobby", PW) is not None


# ---------- Nutzerverwaltung ----------

def test_users_list_has_last_active(alice, bob):
    users = {u["name"]: u for u in alice.get("/api/admin/users").json()["users"]}
    assert users["Bob"]["last_active"] > 0 and "quota_gb" not in users["Bob"]
    assert "pw_hash" not in str(users["Bob"]).replace("'pw_hash': None", "")


def test_last_active_updates_on_use(alice, bob):
    with main.db.db() as c:
        c.execute("UPDATE users SET last_active = 1 WHERE name = 'Bob'")
    bob.get("/api/me")
    assert main.db.find_user("Bob")["last_active"] > 1000


def test_admin_resets_password_with_temp_flag(alice, bob):
    bob_id = main.db.find_user("Bob")["id"]
    r = alice.post(f"/api/admin/users/{bob_id}/reset-password")
    assert r.status_code == 200
    pw = r.json()["password"]
    assert len(pw) >= 8 and pw != PW
    assert bob.get("/api/me").status_code == 401  # old sessions gone
    assert main.db.check_login("Bob", PW) is None
    fresh = Client(main.app)
    login = fresh.post("/api/login", json={"name": "Bob", "password": pw})
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    # user sets their own password: flag gone
    assert fresh.put("/api/me/password", json={"old": pw, "new": "mein-neues-pw-1"}).status_code == 200
    again = Client(main.app).post("/api/login", json={"name": "Bob", "password": "mein-neues-pw-1"})
    assert again.json()["user"]["must_change_password"] is False


def test_only_admin_can_reset_passwords(alice, bob):
    alice_id = main.db.find_user("Alice")["id"]
    assert bob.post(f"/api/admin/users/{alice_id}/reset-password").status_code == 403
    assert main.db.check_login("Alice", PW) is not None  # unveraendert
    assert alice.post("/api/admin/users/99999/reset-password").status_code == 404


def test_name_check_for_new_account_counts_own_name_as_taken(alice):
    r = alice.get("/api/name-check", params={"name": "alice", "new": "true"}).json()
    assert r["state"] == "taken" and r["suggestions"]
