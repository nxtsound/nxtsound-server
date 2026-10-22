"""Backups to other places: destinations, running a backup, keeping the last few, the schedule, and restoring from a destination."""
import datetime
import time

import backup
import backupdrivers
import main
from conftest import PW, Client, make_client


def _local(tmp_path, name="NAS", **kw):
    return {"name": name, "type": "local", "cfg": {"path": str(tmp_path / "bk")}, "schedule": "off", "at": "03:00", "keep": 3, **kw}


def _add(alice, tmp_path, **kw):
    r = alice.put("/api/admin/backups/targets", json=_local(tmp_path, **kw))
    assert r.status_code == 200, r.text
    return r.json()["saved"]


def _wait(alice, tid, secs=10):
    for _ in range(int(secs * 20)):
        t = next(x for x in alice.get("/api/admin/backups").json()["targets"] if x["id"] == tid)
        if not t["state"]["running"] and t["state"]["last_run"]:
            return t
        time.sleep(0.05)
    raise AssertionError("the backup did not finish")


def test_a_destination_is_saved_and_listed(alice, tmp_path):
    t = _add(alice, tmp_path)
    state = alice.get("/api/admin/backups").json()
    assert state["schedules"] == ["off", "6h", "12h", "daily", "weekly"] and set(state["types"]) == {"local", "s3", "webdav", "ftp", "sftp"}
    (listed,) = state["targets"]
    assert listed["id"] == t["id"] and listed["name"] == "NAS" and listed["keep"] == 3 and listed["cfg"] == {"path": str(tmp_path / "bk")}
    assert listed["state"] == {"last_run": 0, "last_ok": 0, "last_error": "", "last_size": 0, "running": False}


def test_secrets_never_leave_the_server_and_empty_means_keep(alice):
    body = {"name": "Cloud", "type": "s3", "cfg": {"endpoint": "https://s3.example.test", "region": "eu-1", "bucket": "b", "access_key": "AK", "secret_key": "TOPSECRET"}, "schedule": "daily", "at": "02:30", "keep": 5}
    r = alice.put("/api/admin/backups/targets", json=body)
    saved = r.json()["saved"]
    assert "TOPSECRET" not in r.text and saved["secrets_set"] == ["secret_key"] and "secret_key" not in saved["cfg"]
    assert alice.get("/api/admin/backups").text.count("TOPSECRET") == 0
    # changing the name without sending the secret keeps it
    again = alice.put("/api/admin/backups/targets", json={**body, "id": saved["id"], "name": "Cloud 2", "cfg": {**body["cfg"], "secret_key": ""}}).json()["saved"]
    assert again["name"] == "Cloud 2" and again["secrets_set"] == ["secret_key"]
    assert backup.get(saved["id"])["cfg"]["secret_key"] == "TOPSECRET"
    # a new secret replaces it
    alice.put("/api/admin/backups/targets", json={**body, "id": saved["id"], "cfg": {**body["cfg"], "secret_key": "NEW"}})
    assert backup.get(saved["id"])["cfg"]["secret_key"] == "NEW"


def test_wrong_settings_are_refused_with_a_reason(alice, tmp_path):
    put = lambda **kw: alice.put("/api/admin/backups/targets", json={**_local(tmp_path), **kw})  # noqa: E731
    assert put(type="gopher").status_code == 422
    assert put(name="  ").status_code == 422 and put(name="x" * 41).status_code == 422
    assert put(name="").status_code == 422
    assert put(schedule="monthly").status_code == 422
    assert put(at="25:00").status_code == 422 and put(at="3:00").status_code == 422
    assert put(keep=0).status_code == 422 and put(keep=1000).status_code == 422
    assert "required" in put(cfg={}).json()["detail"]
    assert put(type="ftp", cfg={"host": "h", "port": "abc", "user": "u", "password": "p"}).status_code == 422
    assert put(id="nope").status_code == 422
    assert alice.get("/api/admin/backups").json()["targets"] == []  # nothing was saved


def test_at_most_ten_destinations(alice, tmp_path):
    for i in range(backup.MAX_TARGETS):
        _add(alice, tmp_path, name=f"T{i}")
    assert alice.put("/api/admin/backups/targets", json=_local(tmp_path, name="eleven")).status_code == 422


def test_a_destination_can_be_removed(alice, tmp_path):
    t = _add(alice, tmp_path)
    assert alice.delete(f"/api/admin/backups/targets/{t['id']}").json()["targets"] == []
    assert alice.delete(f"/api/admin/backups/targets/{t['id']}").status_code == 404


def test_a_backup_is_a_valid_database_on_the_destination(alice, tmp_path):
    t = _add(alice, tmp_path)
    r = backup.run(t["id"])
    assert r["ok"] and backupdrivers.BACKUP_NAME.match(r["name"])
    (f,) = (tmp_path / "bk").iterdir()
    assert main.db.check_backup(f) is None  # a real NxtSound database
    state = alice.get("/api/admin/backups").json()["targets"][0]["state"]
    assert state["last_ok"] >= state["last_run"] > 0 and state["last_error"] == "" and state["last_size"] == f.stat().st_size


def test_only_the_last_few_are_kept_and_other_files_stay(alice, tmp_path, monkeypatch):
    t = _add(alice, tmp_path, keep=2)
    bk = tmp_path / "bk"
    bk.mkdir()
    (bk / "holiday.jpg").write_bytes(b"x")
    for n, stamp in enumerate(("20260101-010101", "20260102-010101", "20260103-010101")):
        (bk / f"nxtsound-{stamp}.db").write_bytes(b"old")
    r = backup.run(t["id"])
    names = sorted(p.name for p in bk.iterdir())
    assert names == sorted(["holiday.jpg", r["name"], "nxtsound-20260103-010101.db"]) and r["deleted"] == 2  # the newest two backups stay


def test_a_failed_backup_is_recorded_and_tells_the_admins(alice, tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    t = _add(alice, tmp_path)
    backup.upsert({**backup.get(t["id"]), "id": t["id"], "cfg": {"path": str(f / "sub")}})  # cannot be a folder
    r = backup.run(t["id"])
    assert r["ok"] is False and "Cannot write" in r["error"]
    state = alice.get("/api/admin/backups").json()["targets"][0]["state"]
    assert "Cannot write" in state["last_error"] and state["last_ok"] == 0
    (note,) = [n for n in alice.get("/api/notifications").json()["items"] if n["kind"] == "backup_failed"]
    assert note["title"] == "Backup failed: NAS" and "Cannot write" in note["body"] and note["nav"] == "#/admin/system" and note["system"]
    # fixed: the next success removes the message
    backup.upsert({**backup.get(t["id"]), "id": t["id"], "cfg": {"path": str(tmp_path / "bk")}})
    assert backup.run(t["id"])["ok"]
    assert not [n for n in alice.get("/api/notifications").json()["items"] if n["kind"] == "backup_failed"]


def test_only_admins_see_the_failure(alice, bob, tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    t = _add(alice, tmp_path)
    backup.upsert({**backup.get(t["id"]), "id": t["id"], "cfg": {"path": str(f / "sub")}})
    backup.run(t["id"])
    assert not [n for n in bob.get("/api/notifications").json()["items"] if n["kind"] == "backup_failed"]


def test_run_in_the_background_and_not_twice_at_once(alice, tmp_path):
    t = _add(alice, tmp_path)
    assert alice.post(f"/api/admin/backups/targets/{t['id']}/run").json() == {"started": True}
    done = _wait(alice, t["id"])
    assert done["state"]["last_ok"] > 0
    backup._running.add(t["id"])
    assert alice.post(f"/api/admin/backups/targets/{t['id']}/run").status_code == 422  # one at a time
    assert alice.post("/api/admin/backups/targets/nope/run").status_code == 422


def test_trying_the_settings_before_saving(alice, tmp_path):
    ok = alice.post("/api/admin/backups/test", json=_local(tmp_path))
    assert ok.status_code == 200 and "writable" in ok.json()["message"] and alice.get("/api/admin/backups").json()["targets"] == []
    bad = alice.post("/api/admin/backups/test", json={**_local(tmp_path), "cfg": {"path": "relative"}})
    assert bad.status_code == 400 and "absolute" in bad.json()["detail"]
    t = _add(alice, tmp_path)
    assert alice.post("/api/admin/backups/test", json={**_local(tmp_path), "id": t["id"]}).status_code == 200


# ---------- Schedule ----------

def _ts(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi).timestamp()


def test_every_six_hours():
    t = {"id": "t1", "schedule": "6h", "state": {"last_ok": _ts(2026, 10, 22, 6, 0), "last_run": _ts(2026, 10, 22, 6, 0)}}
    assert not backup.due(t, _ts(2026, 10, 22, 11, 59)) and backup.due(t, _ts(2026, 10, 22, 12, 0))
    assert backup.due({"id": "t1", "schedule": "6h", "state": {}}, _ts(2026, 10, 22, 12, 0))  # never ran: now


def test_daily_at_a_time():
    base = {"id": "t1", "schedule": "daily", "at": "03:30"}
    ran_today = {**base, "state": {"last_ok": _ts(2026, 10, 22, 3, 31), "last_run": _ts(2026, 10, 22, 3, 31)}}
    assert not backup.due(ran_today, _ts(2026, 10, 22, 23, 0)) and backup.due(ran_today, _ts(2026, 10, 23, 3, 30))
    assert not backup.due(ran_today, _ts(2026, 10, 23, 3, 29))
    missed = {**base, "state": {"last_ok": _ts(2026, 10, 21, 3, 31), "last_run": _ts(2026, 10, 21, 3, 31)}}
    assert backup.due(missed, _ts(2026, 10, 22, 8, 0))  # the server was off at 03:30: the backup is made when it is back


def test_weekly_on_sundays():
    base = {"id": "t1", "schedule": "weekly", "at": "02:00"}  # 2026-10-25 is a Sunday
    done = {**base, "state": {"last_ok": _ts(2026, 10, 25, 2, 5), "last_run": _ts(2026, 10, 25, 2, 5)}}
    assert not backup.due(done, _ts(2026, 10, 30, 12, 0)) and backup.due(done, _ts(2026, 11, 1, 2, 0))
    assert backup.due({**base, "state": {"last_ok": _ts(2026, 10, 18, 2, 5), "last_run": _ts(2026, 10, 18, 2, 5)}}, _ts(2026, 10, 25, 2, 0))


def test_after_a_failure_it_is_tried_again_after_half_an_hour():
    t = {"id": "t1", "schedule": "daily", "at": "03:00", "state": {"last_ok": _ts(2026, 10, 21, 3, 1), "last_run": _ts(2026, 10, 22, 3, 0), "last_error": "x"}}
    assert not backup.due(t, _ts(2026, 10, 22, 3, 20)) and backup.due(t, _ts(2026, 10, 22, 3, 31))


def test_off_and_running_are_never_due():
    assert not backup.due({"id": "t1", "schedule": "off", "state": {}})
    backup._running.add("t2")
    assert not backup.due({"id": "t2", "schedule": "6h", "state": {}})


def test_the_schedule_starts_what_is_due(alice, tmp_path, monkeypatch):
    a = _add(alice, tmp_path, name="A", schedule="6h")
    _add(alice, tmp_path, name="B", schedule="off")
    started = []
    monkeypatch.setattr(backup, "start", lambda tid: started.append(tid))
    assert backup.run_due() == [a["id"]] and started == [a["id"]]


# ---------- Looking into a destination and restoring ----------

def test_files_are_listed_newest_first_and_can_be_deleted(alice, tmp_path):
    t = _add(alice, tmp_path)
    bk = tmp_path / "bk"
    bk.mkdir()
    for stamp in ("20260101-010101", "20260103-010101", "20260102-010101"):
        (bk / f"nxtsound-{stamp}.db").write_bytes(b"x" * 10)
    files = alice.get(f"/api/admin/backups/targets/{t['id']}/files").json()["files"]
    assert [f["name"] for f in files] == ["nxtsound-20260103-010101.db", "nxtsound-20260102-010101.db", "nxtsound-20260101-010101.db"]
    left = alice.post(f"/api/admin/backups/targets/{t['id']}/delete-file", json={"name": "nxtsound-20260103-010101.db"}).json()["files"]
    assert [f["name"] for f in left] == ["nxtsound-20260102-010101.db", "nxtsound-20260101-010101.db"]
    assert alice.post(f"/api/admin/backups/targets/{t['id']}/delete-file", json={"name": "../../etc/passwd"}).status_code == 400


def test_restoring_from_a_destination(alice, tmp_path):
    t = _add(alice, tmp_path)
    main.db.create_user("Kept", PW)
    name = backup.run(t["id"])["name"]
    main.db.create_user("Later", PW)
    r = alice.post(f"/api/admin/backups/targets/{t['id']}/restore", json={"name": name})
    assert r.status_code == 200 and r.json()["safety"].startswith("before-restore-")
    assert main.db.find_user("Kept") and main.db.find_user("Later") is None  # back to the state of the backup
    assert alice.get("/api/me").status_code == 401  # everybody has to log in again
    assert (main.db.DB_PATH.parent / r.json()["safety"]).exists()


def test_a_broken_file_is_not_restored(alice, tmp_path):
    t = _add(alice, tmp_path)
    bk = tmp_path / "bk"
    bk.mkdir()
    (bk / "nxtsound-20260101-010101.db").write_bytes(b"this is not a database")
    r = alice.post(f"/api/admin/backups/targets/{t['id']}/restore", json={"name": "nxtsound-20260101-010101.db"})
    assert r.status_code == 400
    assert main.db.find_user("Alice")  # nothing was changed


def test_backup_endpoints_are_for_admins(alice, bob, anon, tmp_path):
    t = _add(alice, tmp_path)
    for method, path, body in (("get", "/api/admin/backups", None), ("put", "/api/admin/backups/targets", _local(tmp_path)), ("post", "/api/admin/backups/test", _local(tmp_path)),
                               ("post", f"/api/admin/backups/targets/{t['id']}/run", None), ("get", f"/api/admin/backups/targets/{t['id']}/files", None),
                               ("post", f"/api/admin/backups/targets/{t['id']}/restore", {"name": "nxtsound-20260101-010101.db"}), ("delete", f"/api/admin/backups/targets/{t['id']}", None)):
        kw = {"json": body} if body is not None else {}
        assert getattr(bob, method)(path, **kw).status_code == 403, path
        assert getattr(anon, method)(path, **kw).status_code in (401, 403), path
