"""The songs the app may keep offline: last played and most played, each once, only what can be played as a file."""
import db
import library
import main
import plugins
from test_recap import play, track


def uid(name="Alice"):
    return db.find_user(name)["id"]


def ids(client, **params):
    return [t["id"] for t in client.get("/api/me/offline-set", params=params).json()["tracks"]]


def test_nothing_played_nothing_to_keep(alice):
    assert alice.get("/api/me/offline-set").json() == {"tracks": []}


def test_last_played_and_most_played_without_doubles(alice, monkeypatch):
    monkeypatch.setattr(main.library, "existing", lambda i: set(i))
    for n in range(1, 6):  # songs 1..5, played in that order
        play(uid(), track(n), 10 - n)
    for _ in range(5):  # then song 1 five more times (the favourite), and song 2 once more
        play(uid(), track(1), 4.5)
    play(uid(), track(2), 4.4)
    play(uid(), track(5), 4.3)
    play(uid(), track(4), 4.2)
    assert ids(alice, recent=2, top=0) == ["local:t4", "local:t5"]  # the last two different songs
    assert ids(alice, recent=0, top=2) == ["local:t1", "local:t4"]  # played most often (t4 wins the tie with t2 and t5: more recent)
    assert ids(alice, recent=2, top=2) == ["local:t4", "local:t5", "local:t1"]  # the recent ones first, every song once
    assert ids(alice, recent=3, top=3) == ["local:t4", "local:t5", "local:t2", "local:t1"]  # t4 and t5 are both recent and top: once


def test_track_data_comes_along(alice, monkeypatch):
    monkeypatch.setattr(main.library, "existing", lambda i: set(i))
    play(uid(), {**track(1), "thumb": "/api/cover/local:t1"}, 1)
    t = alice.get("/api/me/offline-set").json()["tracks"][0]
    assert (t["id"], t["title"], t["artist"], t["album"], t["duration"], t["thumb"]) == ("local:t1", "Song 1", "Band A", "Album", "3:00", "/api/cover/local:t1")


def test_songs_that_are_gone_are_not_kept(alice, lib):
    alive = library.search_tracks("alpha")[0]["id"]
    play(uid(), {**track(1), "id": alive}, 2)
    play(uid(), track(2), 1)  # a library id that does not exist
    play(uid(), track(3, source="gone"), 1)  # a plugin that is not installed
    assert ids(alice) == [alive]


def test_live_and_episode_sources_are_left_out(alice, monkeypatch):
    monkeypatch.setattr(plugins, "list_plugins", lambda: [{"id": "radio", "categories": [{"id": "radio"}]}, {"id": "demo", "categories": []}])
    monkeypatch.setattr(plugins, "is_enabled_id", lambda i: i.split(":")[0] in ("radio", "demo"))
    monkeypatch.setattr(main.library, "existing", lambda i: set())
    play(uid(), track(1, source="radio"), 2)
    play(uid(), track(2, source="demo"), 1)
    assert ids(alice) == ["demo:t2"]  # a station has no end and cannot be kept; a song from a plain source can


def test_disliked_songs_are_not_kept(alice, monkeypatch):
    monkeypatch.setattr(main.library, "existing", lambda i: set(i))
    play(uid(), track(1), 2)
    play(uid(), track(2), 1)
    db.add_dislike(uid(), "track", "local:t1", "Song 1")
    assert ids(alice) == ["local:t2"]


def test_limits_and_login(alice, anon):
    assert alice.get("/api/me/offline-set", params={"recent": 51}).status_code == 422 and alice.get("/api/me/offline-set", params={"top": -1}).status_code == 422
    assert anon.get("/api/me/offline-set").status_code == 401


def test_each_user_gets_their_own_set(alice, bob, monkeypatch):
    monkeypatch.setattr(main.library, "existing", lambda i: set(i))
    play(uid("Alice"), track(1), 1)
    play(uid("Bob"), track(2), 1)
    assert ids(alice) == ["local:t1"] and ids(bob) == ["local:t2"]
