"""The bell: messages for users, system messages for admins, reading, dismissing; texts in the reader's language."""
import db
from conftest import make_client


def uid(name: str) -> int:
    return db.find_user(name)["id"]


def test_the_bell_is_empty_at_first(alice):
    assert alice.get("/api/notifications").json() == {"unread": 0, "items": []}


def test_admins_see_system_messages_users_do_not(alice, bob):
    db.notify(db.SYSTEM, "disk_low:data", "disk_low", {"name": "data", "free_gb": 0.8, "total_gb": 20, "percent": 4})
    db.notify(uid("Bob"), "follow:1", "follow", {"name": "Alice"})
    a = alice.get("/api/notifications").json()
    b = bob.get("/api/notifications").json()
    assert [(i["kind"], i["system"]) for i in a["items"]] == [("disk_low", True)] and a["unread"] == 1
    assert [(i["kind"], i["system"]) for i in b["items"]] == [("follow", False)] and b["unread"] == 1  # Bob sees his own, not the system message
    assert alice.get("/api/notifications").json()["items"][0]["title"] == "Disk space is running low"
    assert "0.8 GB of 20 GB free (4 %)" in alice.get("/api/notifications").json()["items"][0]["body"]


def test_texts_follow_the_language(alice):
    db.notify(db.SYSTEM, "server_update", "server_update", {"current": "0.1.0", "latest": "0.2.0", "url": "https://example.com"})
    de = alice.get("/api/notifications", headers={"Accept-Language": "de"}).json()["items"][0]
    assert de["title"] == "Server-Update verfügbar: 0.2.0" and "0.1.0" in de["body"] and de["nav"] == "#/admin/system"
    en = alice.get("/api/notifications").json()["items"][0]
    assert en["title"] == "Server update available: 0.2.0"


def test_the_same_message_is_stored_once_a_changed_one_is_new_again(alice):
    aid = uid("Alice")
    assert db.notify(db.SYSTEM, "k", "plugin_update", {"id": "demo", "name": "Demo", "version": "1"}) is True
    assert db.notify(db.SYSTEM, "k", "plugin_update", {"id": "demo", "name": "Demo", "version": "1"}) is False  # nothing new
    alice.post("/api/notifications/seen")
    assert alice.get("/api/notifications").json()["unread"] == 0
    assert db.notify(db.SYSTEM, "k", "plugin_update", {"id": "demo", "name": "Demo", "version": "2"}) is True  # a newer version: unread again
    r = alice.get("/api/notifications").json()
    assert r["unread"] == 1 and len(r["items"]) == 1 and "2" in r["items"][0]["body"]
    assert aid  # (the user exists)


def test_reading_marks_everything_seen_for_that_user_only(alice, bob):
    db.notify(db.SYSTEM, "a", "server_update", {"current": "1", "latest": "2"})
    db.notify(uid("Bob"), "f", "follow", {"name": "X"})
    db.notify(uid("Alice"), "f", "follow", {"name": "Y"})
    assert alice.get("/api/notifications").json()["unread"] == 2 and bob.get("/api/notifications").json()["unread"] == 1
    alice.post("/api/notifications/seen")
    assert alice.get("/api/notifications").json()["unread"] == 0 and bob.get("/api/notifications").json()["unread"] == 1
    assert all(not i["unread"] for i in alice.get("/api/notifications").json()["items"])
    db.notify(uid("Alice"), "g", "follow", {"name": "Z"})  # a new one after reading
    assert alice.get("/api/notifications").json()["unread"] == 1


def test_resolved_messages_disappear(alice):
    db.notify(db.SYSTEM, "disk_low:data", "disk_low", {"name": "data"})
    db.notify(db.SYSTEM, "plugin_update:a", "plugin_update", {"id": "a"})
    db.notify(db.SYSTEM, "plugin_update:b", "plugin_update", {"id": "b"})
    db.resolve_notification(db.SYSTEM, "disk_low:data")
    db.resolve_notifications_like(db.SYSTEM, "plugin_update:", {"plugin_update:b"})
    assert [i["key"] for i in db.notifications_for(uid("Alice"), True)["items"]] == ["plugin_update:b"]


def test_dismissing_follows_the_rules(alice, bob):
    db.notify(db.SYSTEM, "s", "server_update", {"current": "1", "latest": "2"})
    db.notify(uid("Bob"), "b", "follow", {"name": "X"})
    sys_id = alice.get("/api/notifications").json()["items"][-1]["id"] if alice.get("/api/notifications").json()["items"][0]["kind"] == "follow" else alice.get("/api/notifications").json()["items"][0]["id"]
    bob_id = bob.get("/api/notifications").json()["items"][0]["id"]
    assert bob.delete(f"/api/notifications/{sys_id}").status_code == 404  # a user cannot touch system messages
    assert alice.delete(f"/api/notifications/{bob_id}").status_code == 404  # nor a message of someone else
    assert bob.delete(f"/api/notifications/{bob_id}").status_code == 200 and bob.get("/api/notifications").json()["items"] == []
    assert alice.delete(f"/api/notifications/{sys_id}").status_code == 200
    assert alice.delete("/api/notifications/99999").status_code == 404


def test_deleting_a_user_removes_their_messages(alice, bob):
    db.notify(uid("Bob"), "b", "follow", {"name": "X"})
    db.delete_user(uid("Bob"))
    with db.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind = 'follow'").fetchone()[0] == 0


def test_the_bell_needs_login(anon):
    assert anon.get("/api/notifications").status_code == 401 and anon.post("/api/notifications/seen").status_code == 401
