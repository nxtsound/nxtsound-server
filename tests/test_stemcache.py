"""Per-song cache for DJ_STEMS (see stemcache.py): the DB bookkeeping, reading a cached window back without the model, the background
worker yielding to a live request, and that a live bridge render actually uses the cache instead of the model when there is one."""
import numpy as np
import pytest

import db
import djmix
import main
import stemcache
import stems
from conftest import ids_by_title, make_client
from test_djmix import clicks, needs_audio, write_song  # noqa: F401 (shared helpers)


@pytest.fixture(autouse=True)
def _reset_live(monkeypatch):
    """`_live_until` is a module global (see stemcache.py): without this, `mark_live()` in one test would leak into the next."""
    monkeypatch.setattr(stemcache, "_live_until", 0.0)


def _analysis(tid, bpm_, conf=0.9):
    main.db.save_bpm(tid, {"bpm": bpm_, "first_beat": 0.1, "conf": conf, "key": "8A", "key_conf": 0.8, "ver": 2})


def _set_cached_at(video_id: str, ts: int) -> None:
    with main.db.db() as c:
        c.execute("UPDATE stem_cache SET cached_at = ? WHERE video_id = ?", (ts, video_id))


# ---------- db.py bookkeeping ----------

def test_stem_cache_crud(alice, lib):
    tid = ids_by_title(alice)["Alpha"]
    assert db.stem_cache_get(tid) is None
    db.stem_cache_set(tid, 1, 10.0, 20.0, 30.0, 12345)
    row = db.stem_cache_get(tid)
    assert row["ver"] == 1 and row["a_start"] == 10.0 and row["a_len"] == 20.0 and row["b_len"] == 30.0 and row["bytes"] == 12345
    db.stem_cache_set(tid, 2, 0.0, 5.0, 5.0, 1)  # the same track again: replaces, does not duplicate
    assert db.stem_cache_get(tid)["ver"] == 2
    db.stem_cache_touch(tid)  # does not raise
    db.stem_cache_delete(tid)
    assert db.stem_cache_get(tid) is None


def test_stem_cache_stats_and_todo_only_count_confident_local_tracks(alice, lib):
    ids = ids_by_title(alice)
    _analysis(ids["Alpha"], 120)
    _analysis(ids["Beta"], 120, conf=0.1)  # too unsure to ever be a transition partner: not counted
    assert db.stem_cache_stats(djmix.MIN_CONF) == {"total": 1, "done": 0, "bytes": 0}
    assert [r["id"] for r in db.stem_cache_todo(djmix.MIN_CONF, stemcache.CACHE_VER)] == [ids["Alpha"]]
    db.stem_cache_set(ids["Alpha"], stemcache.CACHE_VER, 0, 1, 1, 100)
    assert db.stem_cache_todo(djmix.MIN_CONF, stemcache.CACHE_VER) == []
    assert db.stem_cache_stats(djmix.MIN_CONF) == {"total": 1, "done": 1, "bytes": 100}
    db.stem_cache_set(ids["Alpha"], stemcache.CACHE_VER - 1, 0, 1, 1, 100)  # an old version: made again
    assert [r["id"] for r in db.stem_cache_todo(djmix.MIN_CONF, stemcache.CACHE_VER)] == [ids["Alpha"]]


def test_stem_cache_evict_drops_the_least_recently_used(alice, lib):
    ids = ids_by_title(alice)
    for t in ("Alpha", "Beta", "Gamma"):
        _analysis(ids[t], 120)
        db.stem_cache_set(ids[t], 1, 0, 1, 1, 1000)
    for i, t in enumerate(("Alpha", "Gamma", "Beta")):  # Beta used most recently
        _set_cached_at(ids[t], 1000 + i)
    assert db.stem_cache_evict(0) == []  # 0 = unlimited: never drops anything
    dropped = db.stem_cache_evict(2000)  # room for about 2 of the 3
    assert dropped == [ids["Alpha"]] and db.stem_cache_get(ids["Beta"]) and db.stem_cache_get(ids["Gamma"])


# ---------- Reading and writing the cache itself ----------

@pytest.fixture()
def fake_stems(monkeypatch):
    """A stand-in for the model (see test_djmix.py): scales the audio instead of separating it, and records how much it was given."""
    calls = []
    monkeypatch.setattr(stems, "available", lambda: True)
    monkeypatch.setattr(stems, "vocals", lambda x: (calls.append(len(x) / djmix.SR), (0.4 * x).astype(np.float32))[1])
    monkeypatch.setattr(stemcache, "YIELD_AFTER_CHUNK", 0)  # do not slow the tests down with the real pause between chunks
    return calls


@needs_audio
def test_precompute_caches_the_songs_own_start_and_end_and_read_slices_it_back(clicks, tmp_path, monkeypatch, fake_stems):
    c, ids = clicks
    monkeypatch.setattr(stemcache, "CACHE_DIR", tmp_path / "stem-cache")
    tid = ids["Click A"]
    path = main.library.file_path(tid)
    dur = 70.0
    stemcache._precompute(tid, path, dur)
    row = db.stem_cache_get(tid)
    assert row and row["ver"] == stemcache.CACHE_VER
    assert row["a_start"] == pytest.approx(dur - stemcache.WINDOW) and row["a_len"] == pytest.approx(stemcache.WINDOW)
    assert row["b_len"] == pytest.approx(stemcache.WINDOW) and row["bytes"] > 0
    # each side done in CHUNK-sized pieces (not the whole song, and not the whole 40 s window in one call to the model)
    assert fake_stems == [pytest.approx(stemcache.CHUNK, abs=0.1)] * (2 * round(stemcache.WINDOW / stemcache.CHUNK))

    fake_stems.clear()
    out = stemcache.read(tid, path, "b", 1.0, 5.0, 1.0)  # fully inside the cached "b" (incoming) window
    assert out is not None and out.shape == (int(5.0 * djmix.SR), 2) and not fake_stems  # the model was not asked again

    assert stemcache.read(tid, path, "a", 0.0, 5.0, 1.0) is None  # before the cached "a" (outgoing) window
    assert stemcache.read(tid, path, "b", 35.0, 10.0, 1.0) is None  # reaches past what was cached
    assert stemcache.read(ids["Click B"], path, "b", 0.0, 5.0, 1.0) is None  # no cache for this other track


def test_read_is_none_without_a_cache_or_with_an_old_version(tmp_path, alice, lib):
    tid = ids_by_title(alice)["Alpha"]
    assert stemcache.read(tid, tmp_path / "x.wav", "a", 0.0, 1.0, 1.0) is None
    db.stem_cache_set(tid, stemcache.CACHE_VER - 1, 0.0, 5.0, 5.0, 10)
    assert stemcache.read(tid, tmp_path / "x.wav", "a", 0.0, 1.0, 1.0) is None


@needs_audio
def test_precompute_yields_to_a_live_request_and_caches_nothing(tmp_path, monkeypatch, fake_stems):
    monkeypatch.setattr(stemcache, "CACHE_DIR", tmp_path / "stem-cache")
    path = tmp_path / "song.wav"
    write_song(path, 120, seconds=70)
    stemcache.mark_live()
    stemcache._precompute("local:song", path, 70.0)
    assert db.stem_cache_get("local:song") is None and not fake_stems  # it checked before doing any work, and stopped right there


@needs_audio
def test_a_live_request_partway_through_a_window_stops_it_after_the_current_chunk(tmp_path, monkeypatch, fake_stems):
    """The point of chunking (see stemcache.py): a live render arriving mid-window gets the model within one CHUNK, not after the full WINDOW."""
    monkeypatch.setattr(stemcache, "CACHE_DIR", tmp_path / "stem-cache")
    path = tmp_path / "song.wav"
    write_song(path, 120, seconds=70)
    real_vocals = stems.vocals

    def vocals_then_go_live(x):
        if len(fake_stems) == 2:  # a live render "arrives" after the second chunk
            stemcache.mark_live()
        return real_vocals(x)
    monkeypatch.setattr(stems, "vocals", vocals_then_go_live)
    out = stemcache._separate_window(path, 0.0, stemcache.WINDOW)
    assert out is None  # gave up partway, as soon as it noticed, instead of finishing all 4 chunks
    assert len(fake_stems) == 3  # the chunk in flight when it noticed was allowed to finish; no more after that


def test_live_activity_is_only_remembered_for_a_short_while(monkeypatch):
    monkeypatch.setattr(stemcache, "LIVE_GRACE", 0.05)
    assert not stemcache._live_recently()
    stemcache.mark_live()
    assert stemcache._live_recently()
    import time
    time.sleep(0.1)
    assert not stemcache._live_recently()


# ---------- A live bridge really uses the cache instead of the model ----------

@needs_audio
def test_a_live_bridge_uses_the_precomputed_cache_instead_of_the_model(clicks, tmp_path, monkeypatch, fake_stems):
    c, ids = clicks
    monkeypatch.setattr(stemcache, "CACHE_DIR", tmp_path / "stem-cache")
    for title, tid in ids.items():
        path = main.library.file_path(tid)
        stemcache._precompute(tid, path, 70.0)
    assert len(fake_stems) == 2 * 2 * round(stemcache.WINDOW / stemcache.CHUNK)  # two sides, two songs, chunked - the only calls to the model in this test
    fake_stems.clear()

    def boom(x):
        raise AssertionError("the live model was asked, the cache should have covered this")
    monkeypatch.setattr(stems, "vocals", boom)
    r = c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click B"], "style": "blend"}).json()
    assert not r["skip"] and r["vocals"]  # made successfully, and with the singing led on its own - just without calling `boom`


# ---------- The admin endpoint ----------

def test_admin_dj_stems_is_off_without_the_model(alice):
    assert alice.get("/api/admin/dj-stems").json() == {"available": False}


def test_admin_dj_stems_reports_progress(alice, lib, monkeypatch):
    monkeypatch.setattr(stems, "available", lambda: True)
    monkeypatch.setattr(stemcache, "MAX_BYTES", 5 * 1024 * 1024)
    ids = ids_by_title(alice)
    _analysis(ids["Alpha"], 120)
    _analysis(ids["Beta"], 120)
    db.stem_cache_set(ids["Alpha"], stemcache.CACHE_VER, 0, 1, 1, 42)
    monkeypatch.setattr(stemcache, "_status", {"state": "paused", "total": 2, "done": 1, "bytes": 42, "current": ids["Beta"]})
    r = alice.get("/api/admin/dj-stems").json()
    assert r == {"available": True, "cache_mb_limit": 5, "state": "paused", "total": 2, "done": 1, "bytes": 42, "current": ids["Beta"]}
    assert make_client("Bob").get("/api/admin/dj-stems").status_code == 403
