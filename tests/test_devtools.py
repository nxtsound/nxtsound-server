"""Developer tools: only for admins and only with DEVTOOLS=1; the explanation of a radio adds up to the score it was sorted by."""
import pytest

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


# ---------- Home, lists, fixed dice ----------

def _play(c, title_id, n=1):
    for _ in range(n):
        t = lib_mod.get_track(title_id)
        assert c.post("/api/me/played", json={k: t[k] for k in ("id", "title", "artist", "artist_id", "album", "album_id", "duration", "thumb")}).status_code == 200


def test_a_fixed_dice_repeats_the_radio(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    def rolls(seed):
        return [(t["id"], t["dev"]["parts"]["random"]) for t in _radio(alice, ids["Alpha"], debug=1, rng=seed) if t["dev"]["kind"] == "similar"]
    assert rolls(7) == rolls(7) and [x[1] for x in rolls(7)] != [x[1] for x in rolls(8)]


def test_the_dice_is_not_for_everybody(alice, lib, monkeypatch):
    ids = ids_by_title(alice)
    bob = make_client("Bob")
    monkeypatch.setattr(devtools, "ENABLED", True)
    assert devtools.rng(main.db.find_user("Bob") | {"is_admin": False}, 7) is None and devtools.rng({"is_admin": True}, None) is None
    assert devtools.rng({"is_admin": True}, 7) is not None
    assert bob.get(f"/api/radio/{ids['Alpha']}", params={"rng": 7}).status_code == 200  # accepted, but nothing changes for Bob


def test_home_tells_where_it_came_from(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    _play(alice, ids["Alpha"], 2)
    _play(alice, ids["Epsilon"])
    first = alice.get("/api/home", params={"debug": 1, "refresh": 1}).json()
    assert "dev" in first and first["dev"]["similar"]["cache"] == "refresh"
    seeds = first["dev"]["seeds"]
    assert seeds and seeds[0]["score"] >= seeds[-1]["score"] and {"title", "plays", "liked"} <= set(seeds[0])
    assert all("ms" in w for w in first["dev"]["widgets"])
    second = alice.get("/api/home", params={"debug": 1}).json()
    assert second["dev"]["similar"]["cache"] == "hit"
    fixed = alice.get("/api/home", params={"debug": 1, "rng": 3}).json()
    assert fixed["dev"]["similar"]["cache"] == "fixed dice"  # a mix made with a fixed dice is not what the next visit gets
    assert "dev" not in alice.get("/api/home").json()


def test_home_has_nothing_for_others(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    bob = make_client("Bob")
    assert "dev" not in bob.get("/api/home", params={"debug": 1}).json()


def test_lists_say_why_a_song_is_in_them(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    _play(alice, ids["Alpha"], 3)
    _play(alice, ids["Beta"], 2)
    _play(alice, ids["Gamma"])
    top = alice.get("/api/auto/top", params={"debug": 1}).json()["tracks"]
    assert top[0]["title"] == "Alpha" and top[0]["dev"]["label"] == "3×" and top[0]["dev"]["lines"][0].startswith("played 3×")
    genre = alice.get("/api/auto/genre:Rock", params={"debug": 1}).json()["tracks"]
    assert genre and all(t["dev"]["lines"] == ["genre is “Rock”"] for t in genre)
    assert all("dev" not in t for t in alice.get("/api/auto/top").json()["tracks"])  # only on request


def test_a_day_repeats_the_shuffle_of_that_day(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    one = [t["id"] for t in alice.get("/api/auto/genre:Rock", params={"day": "2026-01-01"}).json()["tracks"]]
    two = [t["id"] for t in alice.get("/api/auto/genre:Rock", params={"day": "2026-01-01"}).json()["tracks"]]
    assert one == two
    assert alice.get("/api/auto/genre:Rock", params={"day": "not-a-day"}).status_code == 422


# ---------- Analysis curves ----------

def _clicks(path, seconds=30, bpm=120):
    import struct
    import wave
    sr, step = 22050, int(22050 * 60 / bpm)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", 20000 if i % step < 300 else 0) for i in range(sr * seconds)))


def test_curves_are_thin_and_scaled(tmp_path):
    import bpm
    if not bpm.available():
        return
    _clicks(tmp_path / "c.wav")
    c = bpm.curves(tmp_path / "c.wav", points=100)
    assert c and 0 < len(c["loudness"]) <= 100 and 0 < len(c["onset"]) <= 100
    assert max(c["loudness"]) == 1.0 and min(c["onset"]) >= 0 and 29 < c["seconds"] < 31
    assert c["loudness_step"] * len(c["loudness"]) == pytest.approx(c["seconds"], abs=1)


def test_analysis_view_is_for_developer_admins_only(alice, lib, monkeypatch):
    ids = ids_by_title(alice)
    assert alice.get(f"/api/dev/analysis/{ids['Alpha']}").status_code == 404  # off: the route is not there
    monkeypatch.setattr(devtools, "ENABLED", True)
    bob = make_client("Bob")
    assert bob.get(f"/api/dev/analysis/{ids['Alpha']}").status_code == 404
    monkeypatch.setattr(main.bpmlib, "curves", lambda path: {"seconds": 1.0, "loudness": [1.0], "loudness_step": 0.5, "onset": [], "onset_step": 0.1})
    main.db.save_bpm(ids["Alpha"], {"bpm": 120.0, "first_beat": 0.1, "conf": 0.8, "key": "8A", "ver": 2})
    r = alice.get(f"/api/dev/analysis/{ids['Alpha']}").json()
    assert r["usable"] and r["stored"]["bpm"] == 120.0 and r["stored"]["key"] == "8A" and r["curves"]["loudness"] == [1.0]
    assert alice.get("/api/dev/analysis/local:00000000-0000-0000-0000-000000000000").status_code == 404


def test_a_song_without_analysis_is_analyzed_when_the_tab_asks(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    ids = ids_by_title(alice)
    monkeypatch.setattr(main.bpmlib, "curves", lambda path: {"seconds": 1.0, "loudness": [1.0], "loudness_step": 0.5, "onset": [], "onset_step": 0.1})
    seen = []
    monkeypatch.setattr(main.bpmlib, "analyze", lambda path: seen.append(path) or {"bpm": 123.0, "first_beat": 0.2, "conf": 0.9, "key": "5A", "ver": main.bpmlib.ANALYSIS_VER})
    assert main.db.get_bpm(ids["Beta"]) is None
    first = alice.get(f"/api/dev/analysis/{ids['Beta']}").json()
    assert first["analyzed_now"] and first["stored"]["bpm"] == 123.0 and main.db.get_bpm(ids["Beta"])["key"] == "5A" and len(seen) == 1
    again = alice.get(f"/api/dev/analysis/{ids['Beta']}").json()
    assert not again["analyzed_now"] and len(seen) == 1  # once is enough


# ---------- Plugin calls ----------

def test_plugin_calls_are_kept_only_with_the_tools_on(alice, monkeypatch):
    devtools.reset()
    devtools.record_call("p", "GET", "/search", {"q": "x"}, 200, 12.3, 5)
    assert devtools.calls() == []  # off: nothing is kept
    monkeypatch.setattr(devtools, "ENABLED", True)
    devtools.record_call("p", "GET", "/search", {"q": "x"}, 200, 12.34, 5, preview="[1]")
    devtools.record_call("p", "GET", "/settings", None, 200, 1, 9, preview='{"password": "secret"}')
    got = devtools.calls()
    assert got[1]["path"] == "/search" and got[1]["preview"] == "[1]" and got[1]["ms"] == 12.3
    assert got[0]["path"] == "/settings" and got[0]["preview"] == ""  # settings can hold passwords
    assert alice.get("/api/dev/plugin-calls").json()["calls"][0]["path"] == "/settings"
    assert make_client("Bob").get("/api/dev/plugin-calls").status_code == 404
    devtools.reset()


def test_calls_to_a_plugin_show_up(alice, monkeypatch):
    import fakeplugin
    import plugins
    monkeypatch.setattr(devtools, "ENABLED", True)
    devtools.reset()
    monkeypatch.setattr(plugins, "_transport_factory", lambda pid: fakeplugin.make())
    assert alice.post("/api/admin/plugins", json={"url": "http://fake-plugin.test", "token": fakeplugin.TOKEN}).status_code == 200
    alice.get("/api/search", params={"q": "first", "source": "plugins"})
    calls = alice.get("/api/dev/plugin-calls").json()["calls"]
    hit = next(c for c in calls if c["path"] == "/search")
    assert hit["status"] == 200 and hit["params"].get("q") == "first" and hit["size"] > 0 and hit["preview"]
    devtools.reset()


# ---------- Snapshot ----------

def test_a_snapshot_keeps_the_logic_and_loses_the_names(alice, lib, monkeypatch):
    import json
    import devsnapshot
    ids = ids_by_title(alice)
    for t, (bpm, key) in {"Alpha": (120, "8A"), "Beta": (122, "9A"), "Epsilon": (121, "8A")}.items():
        main.db.save_bpm(ids[t], {"bpm": bpm, "first_beat": 0.1, "conf": 0.9, "key": key, "key_conf": 0.8, "ver": 2})
    _play(alice, ids["Alpha"], 3)
    _play(alice, ids["Epsilon"])
    alice.post("/api/likes", json={k: lib_mod.get_track(ids["Beta"])[k] for k in ("id", "title", "artist", "artist_id", "album", "album_id", "duration", "thumb")})
    uid = main.db.find_user("Alice")["id"]
    mapping = {}
    snap = devsnapshot.export(uid, mapping)
    text = json.dumps(snap)
    for secret in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Artist A", "Artist B", "Album X", ids["Alpha"], str(lib_mod.MUSIC_DIR)):
        assert secret not in text, secret
    assert "Rock" in text and snap["format"] == 1 and len(snap["tracks"]) == 6

    class Half:  # the same "dice" every time: the scores then depend on the data only
        @staticmethod
        def random():
            return 0.5

    def scores(seed, rename):
        return {rename(t["id"]): t["dev"]["score"] for t in lib_mod.similar(seed, 10, explain=True, dice=Half) if t["dev"]["kind"] == "similar"}

    before = scores(ids["Alpha"], lambda i: mapping[i])
    with main.db.db() as c:  # a fresh instance: nothing of the original left
        c.execute("DELETE FROM users")
        for table in ("lib_tracks", "bpm"):
            c.execute(f"DELETE FROM {table}")
    info = devsnapshot.load(snap, "dev", "secret-1234")
    assert info["tracks"] == len(snap["tracks"])
    after = scores(mapping[ids["Alpha"]], lambda i: i)
    assert before and after == before  # the same neighbours with the same scores, tempo and key included
    dev = make_client("dev")
    top = dev.get("/api/auto/top").json()["tracks"]
    assert [t["title"] for t in top][:1] == [snap["plays"][0]["title"]]  # the user's plays came along
    assert len(dev.get("/api/likes").json()["tracks"]) == 1


def test_export_route_is_for_developer_admins(alice, lib, monkeypatch):
    assert alice.get("/api/dev/export").status_code == 404
    monkeypatch.setattr(devtools, "ENABLED", True)
    r = alice.get("/api/dev/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.json()["server_version"]
    assert make_client("Bob").get("/api/dev/export").status_code == 404
