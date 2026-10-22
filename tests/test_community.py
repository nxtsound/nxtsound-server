"""Community: the admin turns it on, users follow each other, share what they listen to, and see what their friends listen to."""
import pytest

import db
import library
import main
from conftest import make_client
from test_recap import play, track


def uid(name: str) -> int:
    return db.find_user(name)["id"]


@pytest.fixture()
def alive(monkeypatch):
    """The made-up songs of these tests count as existing (the real filtering is tested on its own)."""
    monkeypatch.setattr(main.library, "existing", lambda ids: set(ids))


def on(admin):
    assert admin.put("/api/admin/community", json={"enabled": True}).status_code == 200


def test_the_feature_is_off_until_the_admin_turns_it_on(alice, bob):
    assert alice.get("/api/community").json() == {"enabled": False}
    assert alice.get("/api/community/people").status_code == 403 and alice.post(f"/api/community/follow/{uid('Bob')}").status_code == 403
    assert alice.put("/api/community/share", json={"share": True}).status_code == 403 and alice.get("/api/community/feed").status_code == 403
    assert bob.put("/api/admin/community", json={"enabled": True}).status_code == 403  # only admins decide
    on(alice)
    assert alice.get("/api/community").json() == {"enabled": True, "share": False, "following": [], "followers": 0}
    assert alice.get("/api/admin/community").json()["enabled"] is True


def test_people_and_following(alice, bob):
    on(alice)
    assert [p["name"] for p in alice.get("/api/community/people").json()["people"]] == ["Bob"]
    assert alice.post(f"/api/community/follow/{uid('Bob')}").json() == {"ok": True, "following": True}
    p = alice.get("/api/community/people").json()["people"][0]
    assert p["following"] is True and p["follows_me"] is False and p["shares"] is False
    assert bob.get("/api/community/people").json()["people"][0]["follows_me"] is True
    assert alice.get("/api/community").json()["following"] == [uid("Bob")] and bob.get("/api/community").json()["followers"] == 1
    alice.post(f"/api/community/follow/{uid('Bob')}")  # twice is fine
    assert bob.get("/api/community").json()["followers"] == 1
    assert alice.delete(f"/api/community/follow/{uid('Bob')}").json()["following"] is False
    assert alice.get("/api/community").json()["following"] == []


def test_one_cannot_follow_oneself_or_strangers(alice):
    on(alice)
    assert alice.post(f"/api/community/follow/{uid('Alice')}").status_code == 400
    assert alice.post("/api/community/follow/99999").status_code == 404


def test_following_tells_the_other_one_and_unfollowing_takes_it_back(alice, bob):
    on(alice)
    alice.post(f"/api/community/follow/{uid('Bob')}")
    n = bob.get("/api/notifications").json()
    assert n["unread"] == 1 and n["items"][0]["title"] == "Alice follows you" and n["items"][0]["nav"] == "#/community" and n["items"][0]["system"] is False
    assert bob.get("/api/notifications", headers={"Accept-Language": "de"}).json()["items"][0]["title"] == "Alice folgt dir"
    alice.delete(f"/api/community/follow/{uid('Bob')}")
    assert bob.get("/api/notifications").json()["items"] == []
    assert alice.get("/api/notifications").json()["items"] == []  # nothing for the one who followed


def test_nothing_is_shared_by_default(alice, bob, alive):
    on(alice)
    play(uid("Bob"), track(1), 0.01)
    alice.post(f"/api/community/follow/{uid('Bob')}")
    assert alice.get("/api/community/feed").json()["items"] == []  # Bob has not agreed
    bob.put("/api/community/share", json={"share": True})
    assert [i["track"]["title"] for i in alice.get("/api/community/feed").json()["items"]] == ["Song 1"]
    bob.put("/api/community/share", json={"share": False})  # taking it back hides everything again, past plays included
    assert alice.get("/api/community/feed").json()["items"] == []


def test_only_followed_friends_show_up(alice, bob, alive):
    carol = make_client("Carol")
    on(alice)
    carol.put("/api/community/share", json={"share": True})
    bob.put("/api/community/share", json={"share": True})
    play(uid("Bob"), track(1), 0.01)
    play(uid("Carol"), track(2), 0.01)
    alice.post(f"/api/community/follow/{uid('Carol')}")
    assert [(i["user"]["name"], i["track"]["title"]) for i in alice.get("/api/community/feed").json()["items"]] == [("Carol", "Song 2")]  # not Bob: not followed
    assert carol.get("/api/community/feed").json()["items"] == []  # following nobody


def test_the_feed_is_recent_limited_and_marks_listening_now(alice, bob, alive):
    on(alice)
    bob.put("/api/community/share", json={"share": True})
    alice.post(f"/api/community/follow/{uid('Bob')}")
    play(uid("Bob"), track(1), 20)  # too old (more than 7 days)
    for n in range(2, 8):
        play(uid("Bob"), track(n), 1 + n * 0.01)
    play(uid("Bob"), track(2), 0.0001)  # the same song again counts once
    feed = alice.get("/api/community/feed").json()["items"]
    titles = [i["track"]["title"] for i in feed]
    assert "Song 1" not in titles and len(titles) == db.friends_feed.__defaults__[2] == 3 and titles[0] == "Song 2"  # three different songs per friend, newest first
    assert feed[0]["now"] is True and feed[1]["now"] is False


def test_songs_that_are_gone_are_left_out(alice, bob, lib):
    on(alice)
    bob.put("/api/community/share", json={"share": True})
    alice.post(f"/api/community/follow/{uid('Bob')}")
    alive = library.search_tracks("alpha")[0]
    play(uid("Bob"), {**track(1), "id": alive["id"], "title": "Alpha"}, 0.01)
    play(uid("Bob"), track(2, source="local"), 0.02)  # a library song that does not exist (any more)
    play(uid("Bob"), track(3, source="gone"), 0.03)  # a plugin that is not installed
    assert [i["track"]["title"] for i in alice.get("/api/community/feed").json()["items"]] == ["Alpha"]


def test_the_home_page_shows_what_friends_listen_to_only_when_on(alice, bob, lib):
    play(uid("Alice"), {**track(1), "id": library.search_tracks("alpha")[0]["id"]}, 0.5)
    h = alice.get("/api/home?refresh=1").json()
    assert "community" not in h and not any(w["type"] == "friends" for w in h["widgets"])  # off: no trace
    on(alice)
    bob.put("/api/community/share", json={"share": True})
    play(uid("Bob"), {**track(2), "id": library.search_tracks("beta")[0]["id"], "title": "Beta"}, 0.01)
    h = alice.get("/api/home").json()
    fw = next(w for w in h["widgets"] if w["type"] == "friends")
    assert fw["items"] == [] and h["community"] == {"enabled": True, "following": 0}  # follows nobody yet
    alice.post(f"/api/community/follow/{uid('Bob')}")
    h = alice.get("/api/home").json()  # not cached: the friends' activity is fresh every time
    fw = next(w for w in h["widgets"] if w["type"] == "friends")
    assert [(f["user"]["name"], f["track"]["title"]) for f in fw["items"]] == [("Bob", "Beta")] and h["community"]["following"] == 1


def test_turning_it_off_hides_it_but_keeps_the_follows(alice, bob):
    on(alice)
    alice.post(f"/api/community/follow/{uid('Bob')}")
    alice.put("/api/admin/community", json={"enabled": False})
    assert alice.get("/api/community").json() == {"enabled": False} and alice.get("/api/community/feed").status_code == 403
    on(alice)
    assert alice.get("/api/community").json()["following"] == [uid("Bob")]


def test_deleting_a_user_removes_their_follows(alice, bob):
    on(alice)
    alice.post(f"/api/community/follow/{uid('Bob')}")
    bob.post(f"/api/community/follow/{uid('Alice')}")
    db.delete_user(uid("Bob"))
    assert alice.get("/api/community").json() == {"enabled": True, "share": False, "following": [], "followers": 0}


def test_community_needs_login(anon):
    assert anon.get("/api/community").status_code == 401 and anon.get("/api/community/feed").status_code == 401
