"""Developer tools: only for admins and only with DEVTOOLS=1; the explanation of a radio adds up to the score it was sorted by."""
import devtools
import main
from conftest import ids_by_title, make_client

lib_mod = main.library


def _radio(c, seed, **params):
    return c.get(f"/api/radio/{seed}", params={"limit": 10, **params}).json()["tracks"]


def test_nothing_is_handed_out_by_default(alice, lib):
    ids = ids_by_title(alice)
    assert "devtools" not in alice.get("/api/me").json()
    assert all("dev" not in t for t in _radio(alice, ids["Alpha"], debug=1))  # asking is not enough


def test_admins_get_explanations_when_enabled(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    assert alice.get("/api/me").json()["devtools"] is True
    tracks = _radio(alice, ids["Alpha"], debug=1)
    assert tracks and all("dev" in t for t in tracks)
    assert all("dev" not in t for t in _radio(alice, ids["Alpha"]))  # without debug=1 the answer is the normal one


def test_other_users_get_nothing_even_when_enabled(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    bob = make_client("Bob")  # the second user is no admin
    assert "devtools" not in bob.get("/api/me").json()
    assert all("dev" not in t for t in _radio(bob, ids["Alpha"], debug=1))


def test_the_parts_add_up_to_the_score(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    tracks = [t for t in _radio(alice, ids["Alpha"], debug=1) if t["dev"]["kind"] == "similar"]
    assert tracks
    for t in tracks:
        d = t["dev"]
        assert abs(sum(v for v in d["parts"].values() if v is not None) - d["score"]) < 0.02  # rounded to two places
        assert d["parts"]["random"] <= 2 and d["rank"] >= 1
    assert [t["dev"]["rank"] for t in tracks] == list(range(1, len(tracks) + 1))
    assert tracks[0]["dev"]["score"] >= tracks[-1]["dev"]["score"]  # best first


def test_parts_say_what_matched(alice, lib, monkeypatch):
    monkeypatch.setattr(lib_mod.random, "random", lambda: 0.0)
    ids = ids_by_title(alice)
    parts = {t["title"]: t["dev"]["parts"] for t in lib_mod.similar(ids["Alpha"], 5, explain=True) if t["dev"]["kind"] == "similar"}
    assert parts["Beta"]["artist"] == 3 and parts["Beta"]["album"] == 2 and parts["Beta"]["genre"] == 2 and parts["Beta"]["year"] == 1.5
    assert parts["Epsilon"]["artist"] == 0 and parts["Epsilon"]["genre"] == 2  # same genre only
    assert parts["Beta"]["dj"] is None  # nothing analyzed here


def test_dj_part_shows_when_both_songs_are_analyzed(alice, lib, monkeypatch):
    monkeypatch.setattr(lib_mod.random, "random", lambda: 0.0)
    ids = ids_by_title(alice)
    for t in ("Alpha", "Beta"):
        main.db.save_bpm(ids[t], {"bpm": 120, "first_beat": 0.1, "conf": 0.9, "key": "8A", "key_conf": 0.8, "ver": 2})
    parts = {t["title"]: t["dev"]["parts"] for t in lib_mod.similar(ids["Alpha"], 5, explain=True) if t["dev"]["kind"] == "similar"}
    assert parts["Beta"]["dj"] == 2.0 and parts["Gamma"]["dj"] is None  # 2.5 * 0.8 for a perfect fit


def test_explaining_does_not_change_the_choice(alice, lib, monkeypatch):
    monkeypatch.setattr(lib_mod.random, "random", lambda: 0.5)
    ids = ids_by_title(alice)
    plain = [t["id"] for t in lib_mod.similar(ids["Alpha"], 3)]
    explained = [t["id"] for t in lib_mod.similar(ids["Alpha"], 3, explain=True)]
    assert plain == explained
