"""DJ analysis: key, downbeat, structure (intro/outro) and measurement of the start time on the device."""
import wave

import pytest

import bpm as bpmlib
import main
from conftest import ids_by_title

# ---------- DJ: Tonart, Taktanfang, Struktur ----------


NOTE = {"C": 261.63, "E": 329.63, "G": 392.0, "A": 220.0, "Cn": 261.63}


def _song(path, chord, bpm=120.0, off=0.3, bar_shift=0, total=66):
    """Intro (chord only, 8 s) - main part (kicks, kick on the one louder, up to 48 s) - outro (chord only) - silence from 60 s."""
    import numpy as np
    sr = 22050
    n = sr * total
    t = np.arange(n) / sr
    x = np.zeros(n, dtype=np.float32)
    for f in chord:
        x += 0.03 * np.sin(2 * np.pi * f * t) + 0.02 * np.sin(2 * np.pi * 2 * f * t)
    period = 60 / bpm
    kt = np.arange(int(0.1 * sr)) / sr
    kick = (np.sin(2 * np.pi * (120 - 400 * kt) * kt) * np.exp(-kt * 35)).astype(np.float32)
    k = 0
    while off + k * period < 48:
        s = off + k * period
        if s >= 8:
            i = int(s * sr)
            x[i:i + len(kick)] += kick * (0.9 if (k + bar_shift) % 4 == 0 else 0.35)
        k += 1
    x[sr * 60:] = 0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("int16").tobytes())


@pytest.mark.skipif(not bpmlib.available(), reason="numpy missing")
def test_analysis_finds_key_structure_and_downbeat(tmp_path):
    f = tmp_path / "s.wav"
    _song(f, [NOTE["C"], NOTE["E"], NOTE["G"]], bpm=120, off=0.3)
    r = bpmlib.analyze(f)
    assert r and abs(r["bpm"] - 120) < 0.3
    assert r["key"] == "8B"
    assert abs(r["intro_end"] - 8) <= 1.5 and abs(r["outro_start"] - 48) <= 1.5 and abs(r["audio_end"] - 60) <= 1.5
    period = 0.5
    # bar: the first kick-one after the intro lies at off + 4*period*n; the grid must match that
    err = ((r["first_bar"] - 0.3 + 4 * period / 2) % (4 * period)) - 4 * period / 2
    assert r["bar_ok"] and abs(err) < 0.03


@pytest.mark.skipif(not bpmlib.available(), reason="numpy missing")
def test_analysis_minor_key_and_shifted_bar(tmp_path):
    f = tmp_path / "m.wav"
    _song(f, [NOTE["A"], NOTE["C"], NOTE["E"]], bpm=128, off=0.2, bar_shift=1)  # the one lies a beat later
    r = bpmlib.analyze(f)
    assert r["key"] == "8A"
    period = 60 / 128
    want = 0.2 + 3 * period  # (k + 1) % 4 == 0 -> k = 3
    err = ((r["first_bar"] - want + 2 * period) % (4 * period)) - 2 * period
    assert abs(err) < 0.03


def test_bpm_endpoint_returns_extras_and_reanalyzes_old_rows(bob, lib, monkeypatch):
    v = ids_by_title(bob)["Alpha"]
    main.db.save_bpm(v, {"bpm": 100.0, "first_beat": 0.1, "conf": 0.9})  # old row without the extension
    calls = []

    def fake(path):
        calls.append(1)
        return {"bpm": 120.0, "first_beat": 0.2, "conf": 0.9, "first_bar": 0.7, "bar_ok": True, "key": "8A", "key_conf": 0.8,
                "intro_end": 10.0, "outro_start": 200.0, "audio_end": 220.0, "ver": bpmlib.ANALYSIS_VER}

    monkeypatch.setattr(main, "_bpm_sync", fake)
    d = bob.get(f"/api/bpm/{v}?duration=3:40").json()
    assert d["ready"] and d["usable"] and d["key"] == "8A" and d["first_bar"] == 0.7 and d["outro_start"] == 200.0
    assert bob.get(f"/api/bpm/{v}?duration=3:40").json()["key"] == "8A" and len(calls) == 1


def test_startstat_is_recorded_and_validated(alice, bob):
    assert bob.post("/api/startstat", json={"ms": 1200, "platform": "ios"}).status_code == 200
    assert bob.post("/api/startstat", json={"ms": 800, "platform": "desktop"}).status_code == 200
    assert bob.post("/api/startstat", json={"ms": -5, "platform": "ios"}).status_code == 422
    assert bob.post("/api/startstat", json={"ms": 10, "platform": "tv"}).status_code == 422
    st = alice.get("/api/admin/dashboard").json()["stream_stats"]
    assert st["client_ios"]["n"] >= 1 and st["client_desktop"]["n"] >= 1
