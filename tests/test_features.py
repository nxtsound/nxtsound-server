"""Admin-Backup."""
import sqlite3

import main
from conftest import vid


def _playlist(client, ids):
    pid = client.post("/api/playlists", json={"name": "Mix", "tracks": [{"id": v, "title": v} for v in ids]}).json()["id"]
    return pid


def test_backup_is_admin_only(alice, bob, anon):
    assert bob.get("/api/admin/backup").status_code == 403
    assert anon.get("/api/admin/backup").status_code == 401


def test_backup_is_a_consistent_db_without_sessions(alice, tmp_path):
    _playlist(alice, [vid(90)])
    r = alice.get("/api/admin/backup")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and ".db" in r.headers["content-disposition"]
    f = tmp_path / "b.db"
    f.write_bytes(r.content)
    c = sqlite3.connect(f)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("SELECT name FROM users").fetchone()[0] == "Alice"
    assert c.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    c.close()
    assert alice.get("/api/me").status_code == 200  # the running session stays alive
    assert not list(main.tempfile.gettempdir() and __import__("pathlib").Path(main.tempfile.gettempdir()).glob("backup-*.db"))
