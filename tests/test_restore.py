"""Backup einspielen (Admin): Rundreise, Pruefungen, Rechte."""
import sqlite3

import main
from conftest import make_client


def _names(c):
    return [p["name"] for p in c.get("/api/playlists").json()["playlists"]]


def test_restore_roundtrip(alice, lib, tmp_path):
    alice.post("/api/playlists", json={"name": "Vorher"})
    backup = alice.get("/api/admin/backup").content
    alice.post("/api/playlists", json={"name": "Nachher"})
    assert sorted(_names(alice)) == ["Nachher", "Vorher"]
    r = alice.post("/api/admin/restore", content=backup, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200 and r.json()["safety"].startswith("before-restore-")
    assert (main.db.DB_PATH.parent / r.json()["safety"]).exists()
    assert alice.get("/api/playlists").status_code == 401  # sessions ended
    again = make_client("Alice")
    assert _names(again) == ["Vorher"]
    main.library.scan(full=True)  # the scan after restoring (in the server it runs in the background) puts the library in order
    assert again.get("/api/search", params={"q": "alpha"}).json()["items"]


def test_restore_rejects_garbage_and_foreign_databases(alice, tmp_path):
    h = {"Content-Type": "application/octet-stream"}
    assert alice.post("/api/admin/restore", content=b"this is not a database", headers=h).status_code == 400
    other = tmp_path / "x.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE foo(a)")
    c.commit()
    c.close()
    r = alice.post("/api/admin/restore", content=other.read_bytes(), headers=h)
    assert r.status_code == 400 and "not a backup" in r.json()["detail"]
    assert _names(alice) == []  # nothing changed, the session stays valid


def test_restore_rejects_backup_without_admin(alice):
    backup = alice.get("/api/admin/backup").content
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "noadmin.db"
    p.write_bytes(backup)
    c = sqlite3.connect(p)
    c.execute("UPDATE users SET is_admin = 0")
    c.commit()
    c.close()
    r = alice.post("/api/admin/restore", content=p.read_bytes(), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 400 and "no admin" in r.json()["detail"]


def test_restore_admin_only(alice):
    bob = make_client("Bob")
    assert bob.post("/api/admin/restore", content=b"x", headers={"Content-Type": "application/octet-stream"}).status_code == 403


def test_restore_accepts_backups_above_the_normal_body_limit(alice):
    """The general request limit (2 MB) must not block real-world backups."""
    main.db.set_setting("padding", "x" * (3 * 1024 * 1024))
    backup = alice.get("/api/admin/backup").content
    assert len(backup) > main.MAX_BODY
    r = alice.post("/api/admin/restore", content=backup, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    assert alice.post("/api/playlists", json={"name": "x"}).status_code == 401  # sessions ended after the restore


def test_restore_as_multipart_form_data(alice):
    alice.post("/api/playlists", json={"name": "Vorher"})
    backup = alice.get("/api/admin/backup").content
    alice.post("/api/playlists", json={"name": "Nachher"})
    r = alice.post("/api/admin/restore", files={"file": ("backup.db", backup, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert _names(make_client("Alice")) == ["Vorher"]
