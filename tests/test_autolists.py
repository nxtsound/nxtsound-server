"""Automatic lists ("For you"): computed from listening history, likes and library."""
import time

import main
from conftest import ids_by_title, write_wav


def _play(alice, tid, n=1, ago_days=0):
    track = next(t for t in alice.get("/api/search", params={"q": "a"}).json()["items"] + alice.get("/api/search", params={"q": "e"}).json()["items"] if t["id"] == tid)
    for _ in range(n):
        assert alice.post("/api/me/played", json=track).status_code == 200
    if ago_days:
        with main.db.db() as c:
            c.execute("UPDATE plays SET played_at = ?", (int(time.time() - ago_days * 86400),))


def _names(alice):
    return {l["id"]: l for l in alice.get("/api/auto").json()["lists"]}


def test_auto_lists_without_history_offer_library_groups(alice, lib):
    lists = _names(alice)
    assert "top" not in lists and "rediscover" not in lists
    assert lists["genre:Rock"]["name"] == "Rock Mix" and lists["genre:Rock"]["count"] == 4
    assert lists["decade:2020"]["name"] == "The 2020s" and lists["new"]["name"] == "Recently added"
    g = alice.get("/api/auto/genre:Rock").json()
    assert {t["title"] for t in g["tracks"]} == {"Alpha", "Beta", "Gamma", "Epsilon"}
    assert alice.get("/api/auto/gibtsnicht").status_code == 404


def test_top_list_counts_plays_and_artist_mix_appears(alice, lib):
    ids = ids_by_title(alice)
    _play(alice, ids["Alpha"], 3)
    _play(alice, ids["Beta"], 2)
    _play(alice, ids["Epsilon"], 1)
    lists = _names(alice)
    assert [t["title"] for t in alice.get("/api/auto/top").json()["tracks"]] == ["Alpha", "Beta", "Epsilon"]
    mix_id = next(i for i in lists if i.startswith("artist:"))
    mix = alice.get("/api/auto/" + mix_id).json()
    assert mix["name"].startswith("Mix: ") and {"Alpha", "Beta", "Gamma"} <= {t["title"] for t in mix["tracks"]}


def test_rediscover_has_old_plays_and_forgotten_likes(alice, lib):
    ids = ids_by_title(alice)
    _play(alice, ids["Alpha"], 2, ago_days=60)
    _play(alice, ids["Beta"], 1, ago_days=60)
    _play(alice, ids["Gamma"], 1, ago_days=60)
    assert {t["title"] for t in alice.get("/api/auto/rediscover").json()["tracks"]} == {"Alpha", "Beta", "Gamma"}
    _play(alice, ids["Alpha"], 1)  # just played: drops out
    assert {t["title"] for t in alice.get("/api/auto/rediscover").json()["tracks"]} == {"Beta", "Gamma"}


def test_auto_lists_respect_dislikes_and_are_stable_per_day(alice, lib):
    first = [t["id"] for t in alice.get("/api/auto/genre:Rock").json()["tracks"]]
    assert first == [t["id"] for t in alice.get("/api/auto/genre:Rock").json()["tracks"]]
    ids = ids_by_title(alice)
    assert alice.post("/api/dislikes", json={"kind": "track", "id": ids["Epsilon"], "title": "Epsilon"}).status_code == 200
    assert ids["Epsilon"] not in [t["id"] for t in alice.get("/api/auto/genre:Rock").json()["tracks"]]


def test_auto_lists_need_login(anon):
    assert anon.get("/api/auto").status_code == 401
