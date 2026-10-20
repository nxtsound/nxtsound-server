"""Listening time, admin dashboard, lyrics, tempo detection (DJ mode)."""
import wave

import pytest

import bpm as bpmlib
import main
from conftest import ids_by_title, make_client, vid


# ---------- Listening time and dashboard ----------

def test_listen_adds_real_seconds_and_dashboard_sums_them(alice, bob):
    assert alice.post("/api/listen", json={"seconds": 12.5}).status_code == 200
    assert bob.post("/api/listen", json={"seconds": 30}).status_code == 200
    d = alice.get("/api/admin/dashboard").json()
    assert d["seconds_total"] == 42.5 and d["seconds_7d"] == 42.5
    assert d["users"] == 2 and d["version"] == main.VERSION_INFO["version"]
    assert d["disk"]["total"] > 0 and d["library"]["tracks"] == 0


def test_listen_rejects_negative_and_huge_values(bob):
    assert bob.post("/api/listen", json={"seconds": -1}).status_code == 422
    assert bob.post("/api/listen", json={"seconds": 301}).status_code == 422
    assert bob.post("/api/listen", json={}).status_code == 422


def test_dashboard_is_admin_only_and_listen_needs_login(alice, bob):
    assert bob.get("/api/admin/dashboard").status_code == 403
    anon = make_client.__globals__["Client"](main.app)
    assert anon.post("/api/listen", json={"seconds": 5}).status_code == 401


# ---------- Songtexte ----------

def test_parse_lrc_sorts_and_ignores_junk():
    out = main.parse_lrc("[01:05.50]Welt\n[00:12.3] Hallo \nTitel: egal\n[00:30]Mitte")
    assert out == [[12.3, "Hallo"], [30.0, "Mitte"], [65.5, "Welt"]]


def test_lyrics_endpoint_fetches_once_and_caches(bob, monkeypatch):
    calls = []

    def fake(title, artist, duration):
        calls.append((title, artist, duration))
        return {"found": True, "synced": [[0.0, "Zeile"]], "plain": "Zeile"}

    monkeypatch.setattr(main, "_fetch_lyrics_sync", fake)
    q = f"/api/lyrics/{vid(5)}?title=Song&artist=Band&duration=3:45"
    assert bob.get(q).json()["found"] is True
    assert bob.get(q).json()["synced"] == [[0.0, "Zeile"]]
    assert calls == [("Song", "Band", 225)]


def test_lyrics_not_found_is_cached_but_errors_retry_soon(bob, monkeypatch):
    seq = iter([{"found": False}, {"found": True, "synced": [], "plain": "x"}])
    monkeypatch.setattr(main, "_fetch_lyrics_sync", lambda *a: next(seq))
    q = f"/api/lyrics/{vid(6)}?title=Song&artist=Band"
    assert bob.get(q).json() == {"found": False}
    assert bob.get(q).json() == {"found": False}  # not found: do not ask again for a week
    # a network error is only remembered briefly
    seq2 = iter([{"found": False, "error": True}, {"found": True, "synced": [], "plain": "ok"}])
    monkeypatch.setattr(main, "_fetch_lyrics_sync", lambda *a: next(seq2))
    q2 = f"/api/lyrics/{vid(7)}?title=Song&artist=Band"
    assert bob.get(q2).json()["error"] is True
    main.db.save_lyrics(vid(7), {"found": False, "error": True})
    with main.db.db() as c:
        c.execute("UPDATE lyrics SET fetched_at = 0 WHERE video_id = ?", (vid(7),))
    assert bob.get(q2).json()["found"] is True


def test_lyrics_can_be_switched_off(bob, monkeypatch):
    monkeypatch.setattr(main, "LYRICS_ENABLED", False)
    monkeypatch.setattr(main, "_fetch_lyrics_sync", lambda *a: pytest.fail("must not ask"))
    assert bob.get(f"/api/lyrics/{vid(8)}?title=Song").json() == {"found": False}


# ---------- Tempo-Erkennung ----------

def _click_track(path, bpm, offset, seconds=60):
    import numpy as np
    sr = 22050
    t = np.zeros(sr * seconds, dtype=np.float32)
    period = 60 / bpm
    k = 0
    tt = np.arange(int(0.08 * sr)) / sr
    while offset + k * period < seconds - 0.3:
        i = int((offset + k * period) * sr)
        t[i:i + len(tt)] += np.sin(2 * np.pi * (110 - 300 * tt) * tt) * np.exp(-tt * 45) * 0.8
        k += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(t, -1, 1) * 32767).astype("int16").tobytes())


@pytest.mark.skipif(not bpmlib.available(), reason="numpy missing")
@pytest.mark.parametrize("bpm,off", [(128, 0.3), (120, 0.1), (140.5, 0.17)])
def test_bpm_analysis_finds_tempo_and_first_beat(tmp_path, bpm, off):
    f = tmp_path / "t.wav"
    _click_track(f, bpm, off)
    r = bpmlib.analyze(f)
    assert r and abs(r["bpm"] - bpm) < 0.3 and r["conf"] > 0.8
    period = 60 / bpm
    err = ((r["first_beat"] - off + period / 2) % period) - period / 2
    assert abs(err) < 0.02  # unter 20 ms daneben


@pytest.mark.skipif(not bpmlib.available(), reason="numpy missing")
def test_bpm_analysis_rejects_noise(tmp_path):
    import numpy as np
    f = tmp_path / "n.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes((np.random.default_rng(1).standard_normal(22050 * 60) * 3000).astype("int16").tobytes())
    r = bpmlib.analyze(f)
    assert r is None or r["conf"] < main.BPM_MIN_CONF


def test_bpm_endpoint_analyzes_library_file_once_and_caches_result(bob, lib, monkeypatch):
    calls = []
    monkeypatch.setattr(main.bpmlib, "available", lambda: True)
    monkeypatch.setattr(main.bpmlib, "analyze", lambda p: calls.append(p) or {"bpm": 128.0, "first_beat": 0.2, "conf": 0.9})
    tid = ids_by_title(bob)["Alpha"]
    q = f"/api/bpm/{tid}?duration=3:30"
    r = bob.get(q).json()
    assert r["ready"] and r["usable"] and r["bpm"] == 128.0 and r["first_beat"] == 0.2
    assert bob.get(q).json()["bpm"] == 128.0 and len(calls) == 1  # analyzed only once
    assert bob.get(f"/api/bpm/{vid(99)}?duration=3:30").status_code == 404  # unknown track


def test_bpm_endpoint_marks_unclear_and_long_tracks_unusable(bob, lib, monkeypatch):
    ids = ids_by_title(bob)
    monkeypatch.setattr(main.bpmlib, "available", lambda: True)
    monkeypatch.setattr(main.bpmlib, "analyze", lambda p: {"bpm": 90.0, "first_beat": 0.1, "conf": 0.1})
    assert bob.get(f"/api/bpm/{ids['Beta']}?duration=3:30").json()["usable"] is False
    monkeypatch.setattr(main.bpmlib, "analyze", lambda p: pytest.fail("do not analyze long sets"))
    assert bob.get(f"/api/bpm/{ids['Gamma']}?duration=1:02:10").json() == {"ready": True, "usable": False}
