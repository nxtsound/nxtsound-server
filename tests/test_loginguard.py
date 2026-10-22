"""Login protection: the admin's numbers, blocks that last and survive a restart, lifting blocks, and the admin API."""
import re
import time

import main
from conftest import PW, Client


def _fail(c, name="Chef", n=1):
    return [c.post("/api/login", json={"name": name, "password": "falsch-falsch"}).status_code for _ in range(n)]


def _set(alice, attempts, minutes):
    r = alice.put("/api/admin/security", json={"max_attempts": attempts, "block_minutes": minutes})
    assert r.status_code == 200
    return r.json()


def test_the_defaults(alice):
    s = alice.get("/api/admin/security").json()
    assert (s["max_attempts"], s["block_minutes"]) == (5, 5) and s["blocks"] == []
    assert s["defaults"] == {"max_attempts": 5, "block_minutes": 5}


def test_the_admin_decides_how_many_attempts(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 2, 5)
    assert _fail(anon, n=2) == [401, 401]
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429  # even the right password is blocked


def test_zero_attempts_means_no_blocking(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 0, 5)
    assert set(_fail(anon, n=30)) == {401}
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200


def test_the_block_lasts_as_long_as_set(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 30)
    _fail(anon)
    (block,) = alice.get("/api/admin/security").json()["blocks"]
    assert abs(block["until"] - (time.time() + 30 * 60)) < 5
    r = anon.post("/api/login", json={"name": "Chef", "password": PW})
    assert r.status_code == 429
    assert 29 <= int(re.search(r"in (\d+) min", r.json()["detail"]).group(1)) <= 31


def test_the_block_ends_by_itself(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 5)
    _fail(anon)
    with main.db.db() as c:
        c.execute("UPDATE login_blocks SET until = ?", (int(time.time()) - 1,))
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200


def test_a_block_survives_a_restart(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 5)
    _fail(anon)
    main.loginguard.reset()  # what a restart forgets: the counted failures
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429


def test_a_correct_login_forgives_earlier_typos(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 3, 5)
    _fail(anon, n=2)
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200
    assert _fail(Client(main.app), n=2) == [401, 401]  # counting started over


def test_one_ip_across_many_names_is_blocked_later(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 2, 5)
    codes = [anon.post("/api/login", json={"name": f"n{i}", "password": "falsch-falsch"}).status_code for i in range(8)]
    assert codes == [401] * 8  # eight different names: no single name reached its limit, the IP limit (4 x 2) is reached with the last one
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 429  # now the whole IP is blocked
    assert [b["name"] for b in alice.get("/api/admin/security").json()["blocks"]] == [""]


def test_the_admin_can_lift_blocks(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 5)
    _fail(anon)
    (block,) = alice.get("/api/admin/security").json()["blocks"]
    assert block["name"] == "chef" and block["ip"] == "testclient"
    r = alice.post("/api/admin/security/unblock", json={"ip": block["ip"], "name": block["name"]})
    assert r.status_code == 200 and r.json()["lifted"] == 1 and r.json()["blocks"] == []
    assert anon.post("/api/login", json={"name": "Chef", "password": PW}).status_code == 200


def test_lifting_all_blocks(alice, anon):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 5)
    _fail(anon, "Chef")
    _fail(anon, "Other")
    assert len(alice.get("/api/admin/security").json()["blocks"]) >= 2
    assert alice.post("/api/admin/security/unblock", json={}).json()["blocks"] == []


def test_the_numbers_are_validated(alice):
    assert alice.put("/api/admin/security", json={"max_attempts": -1, "block_minutes": 5}).status_code == 422
    assert alice.put("/api/admin/security", json={"max_attempts": 101, "block_minutes": 5}).status_code == 422
    assert alice.put("/api/admin/security", json={"max_attempts": 5, "block_minutes": 0}).status_code == 422
    assert alice.put("/api/admin/security", json={"max_attempts": 5, "block_minutes": 1441}).status_code == 422
    assert alice.put("/api/admin/security", json={"max_attempts": 5}).status_code == 422


def test_a_broken_stored_value_falls_back_to_the_default(alice):
    main.db.set_setting("login_max_attempts", "lots")
    main.db.set_setting("login_block_minutes", "-3")
    assert main.loginguard.settings() == (main.loginguard.DEFAULT_ATTEMPTS, main.loginguard.DEFAULT_BLOCK_MINUTES)


def test_only_admins_reach_the_login_protection(alice, bob, anon):
    assert bob.get("/api/admin/security").status_code == 403
    assert bob.put("/api/admin/security", json={"max_attempts": 5, "block_minutes": 5}).status_code == 403
    assert bob.post("/api/admin/security/unblock", json={}).status_code == 403
    assert anon.get("/api/admin/security").status_code == 401


def test_the_password_check_of_the_account_is_protected_too(alice):
    _set(alice, 2, 5)
    for _ in range(2):
        assert alice.put("/api/me/password", json={"old": "nope-nope", "new": "ganz-neu-1234"}).status_code == 403
    assert alice.put("/api/me/password", json={"old": PW, "new": "ganz-neu-1234"}).status_code == 429


def test_blocks_are_not_part_of_a_backup(alice, anon, tmp_path):
    main.db.create_user("Chef", PW)
    _set(alice, 1, 5)
    _fail(anon)
    target = tmp_path / "backup.db"
    main.db.backup_to(target)
    import sqlite3
    c = sqlite3.connect(target)
    assert c.execute("SELECT COUNT(*) FROM login_blocks").fetchone()[0] == 0
    c.close()
