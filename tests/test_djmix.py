"""DJ bridges made on the server: the plan, lining the pieces up, the mix and the file that is made of it."""
import shutil
import wave

import numpy as np
import pytest

import bpm
import djmix
import main
from conftest import make_client

needs_audio = pytest.mark.skipif(not (djmix.available() and shutil.which("ffmpeg")), reason="needs numpy and ffmpeg")


def info(bpm_=120.0, **kw):
    """An analysis result like the tempo analysis stores it."""
    return {"bpm": bpm_, "first_beat": 0.5, "conf": 1.0, "first_bar": 0.5, "bar_ok": True, "key": "8A", "key_conf": 0.8,
            "intro_end": 8.0, "outro_start": 200.0, "audio_end": 235.0, **kw}


# ---------- Plan ----------

def test_the_plan_matches_tempo_and_lands_on_the_grid():
    a, b = info(120.0), info(124.0, first_bar=1.0, first_beat=1.0)
    p = djmix.plan(a, b, 240.0, "blend")
    assert not p["skip"] and p["lock"] and p["style"] == "blend"
    assert p["rate"] == pytest.approx(120 / 124, abs=1e-4)  # the new song is played slower
    bar = 4 * 60 / 120
    assert (p["t_a"] - 0.5) % bar == pytest.approx(0, abs=0.01) or (p["t_a"] - 0.5) % bar == pytest.approx(bar, abs=0.01)  # on the old song's bars
    assert p["mix_sec"] / p["beat_a"] == pytest.approx(p["info"]["mb"]) and 8 <= p["info"]["mb"] <= 32
    assert p["a_start"] == pytest.approx(p["t_a"] - djmix.LEAD, abs=0.002)
    assert p["t_a"] >= a["outro_start"] and p["t_a"] + p["mix_sec"] <= 235.0 - 0.8 + 0.01  # at the outro, and it fits before the end
    assert 0 <= p["x"] < 4 * 60 / 124  # the new song comes in near its start


def test_half_and_double_tempo_count_as_a_match():
    p = djmix.plan(info(70.0), info(140.0), 240.0, "filter")
    assert p["lock"] and p["bpm_b"] == 70.0 and p["rate"] == 1.0


def test_tempos_too_far_apart_get_an_echo_without_stretching():
    p = djmix.plan(info(120.0), info(90.0), 240.0, "blend")
    assert not p["lock"] and p["rate"] == 1.0 and p["style"] == "echo" and p["info"]["mb"] == 8  # whatever was asked for


def test_no_plan_without_a_sure_analysis_or_without_room():
    assert djmix.plan(info(conf=0.2), info(), 240.0)["skip"] and djmix.plan(info(), info(bpm_=0.0), 240.0)["skip"]
    short = djmix.plan(info(outro_start=None, audio_end=None), info(), 7.0)
    assert short["skip"] and "ends" in short["why"]


def test_a_loud_start_or_a_clash_of_keys_means_a_short_transition_without_bass_swap():
    hot = djmix.plan(info(), info(intro_end=1.0), 240.0, "auto", rng=__import__("random").Random(1))
    assert hot["info"]["hot"] and hot["info"]["mb"] <= 8 and hot["style"] != "blend"
    clash = djmix.plan(info(key="8A"), info(key="2B"), 240.0, "auto", rng=__import__("random").Random(1))
    assert clash["info"]["fit"] == -1 and clash["style"] != "blend"


def test_the_style_is_not_the_same_twice_and_is_repeatable():
    import random
    styles = {djmix.pick_style(True, 1, False, "auto", "blend,loop", random.Random(i)) for i in range(60)}
    assert not styles & {"blend", "loop"} and styles == {"filter", "echo", "bass", "cut", "reverb"}
    assert {djmix.pick_style(True, 1, False, "auto", "", random.Random(i)) for i in range(80)} == set(djmix.STYLES)
    assert "bass" not in {djmix.pick_style(True, -1, False, "auto", "", random.Random(i)) for i in range(80)}
    assert djmix.pick_style(True, 0, False, "auto", "", random.Random("x")) == djmix.pick_style(True, 0, False, "auto", "", random.Random("x"))
    assert djmix.pick_style(True, 0, False, "filter") == "filter" and djmix.pick_style(False, 0, False, "filter") == "echo"


# ---------- Lining up ----------

def _onsets(frames, period=43, n=400, shift=0):
    e = np.zeros(n, dtype=np.float32)
    for i in range(10, n - 10, period):
        e[i + shift] = 1.0
        e[i + shift + 1] = 0.5
    return e


def test_the_offset_between_two_beats_is_found():
    for shift in (-4, -1, 0, 2, 5):
        got, strength = djmix.align_offset(_onsets(0), _onsets(0, shift=shift))
        assert got == pytest.approx(shift, abs=0.35) and strength > 0.5


def test_noise_gives_no_confident_offset():
    rng = np.random.default_rng(1)
    _, strength = djmix.align_offset(rng.random(400).astype(np.float32), rng.random(400).astype(np.float32))
    assert strength < djmix.MIN_ALIGN_STRENGTH * 2


# ---------- Mix ----------

def _pieces(seconds=40, sr=djmix.SR):
    t = np.arange(int(sr * seconds)) / sr
    a = np.stack([0.4 * np.sin(2 * np.pi * 220 * t), 0.4 * np.sin(2 * np.pi * 223 * t)], axis=1).astype(np.float32)
    b = (np.random.default_rng(2).standard_normal((len(t), 2)) * 0.1).astype(np.float32)
    return a, b


PLAN = {"mix_sec": 8.0, "beat_a": 0.5, "style": "fade", "swap": 4, "gap": True}


@pytest.mark.parametrize("style", ["fade", "blend", "filter", "echo", "loop", "bass", "cut", "reverb"])
def test_the_bridge_starts_with_the_old_song_and_ends_with_the_new_one(style):
    a, b = _pieces()
    out = djmix.mix_pieces(a, b, {**PLAN, "style": style})
    lead, mix = int(djmix.LEAD * djmix.SR), int(PLAN["mix_sec"] * djmix.SR)
    assert out.shape == (int(round((djmix.LEAD + 8.0 + djmix.TAIL) * djmix.SR)), 2) and out.dtype == np.float32
    assert np.array_equal(out[:lead], a[:lead])  # the same music as the old song's file: the player fades over here
    tail = out[lead + mix + djmix.SR: lead + mix + 5 * djmix.SR]
    want = b[mix + djmix.SR: mix + 5 * djmix.SR]
    assert np.corrcoef(tail[:, 0], want[:, 0])[0, 1] > 0.98  # and the new song's own sound after it
    assert np.abs(out).max() <= 1.0
    assert np.abs(out[lead + mix + int(djmix.SR * 3):]).max() > 0.05  # not silent


def test_the_new_song_is_silent_before_the_transition_and_moves_with_the_offset():
    a, b = _pieces()
    quiet = np.zeros_like(a)
    out = djmix.mix_pieces(quiet, b, PLAN)
    assert np.abs(out[: int(djmix.LEAD * djmix.SR) - 10]).max() == 0
    # after the transition the new song is heard as it is: its sample k is at LEAD + k, or 50 ms earlier for a correction of -0.05
    k = int(djmix.SR * (djmix.LEAD + PLAN["mix_sec"] + 3))
    early = djmix.mix_pieces(quiet, b, PLAN, -0.05)
    assert np.allclose(out[k:k + 1000], b[k - int(djmix.LEAD * djmix.SR):k - int(djmix.LEAD * djmix.SR) + 1000], atol=1e-3)
    shift = int(round((djmix.LEAD - 0.05) * djmix.SR))
    assert np.allclose(early[k:k + 1000], b[k - shift:k - shift + 1000], atol=1e-3)


# ---------- With real files ----------

def write_song(path, bpm_, seconds=70, note=220, offset=0.0):
    """A song with a clear beat (a kick on every beat, a hat on the off beats, a quiet start and an outro), 11 kHz mono."""
    sr = 11025
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = 0.25 * np.sin(2 * np.pi * note * t)
    beat = 60 / bpm_
    L = int(0.12 * sr)
    env = np.exp(-np.arange(L) / (0.03 * sr))
    for k in range(int((seconds - 1) / beat)):
        s = int((k * beat + offset) * sr)
        if s + L > n:
            break
        x[s:s + L] += 0.7 * env * np.sin(2 * np.pi * 60 * np.arange(L) / sr)
        if k % 2:
            x[s:s + L] += 0.15 * env * np.random.default_rng(k).standard_normal(L)
    x[: sr * 4] *= np.linspace(0.2, 1, sr * 4)
    x[-sr * 8:] *= np.linspace(1, 0.1, sr * 8)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 30000).astype(np.int16).tobytes())


@needs_audio
def test_the_offset_of_real_pieces_is_measured(tmp_path):
    write_song(tmp_path / "a.wav", 120, 30)
    write_song(tmp_path / "b.wav", 120, 30, offset=0.04)  # B's beats fall 40 ms late
    shift, strength = djmix._measure_offset(tmp_path / "a.wav", tmp_path / "b.wav", {"t_a": 4.0, "x": 4.0, "mix_sec": 8.0, "rate": 1.0})
    assert shift == pytest.approx(0.04, abs=0.012) and strength > 0.3
    same, _ = djmix._measure_offset(tmp_path / "a.wav", tmp_path / "a.wav", {"t_a": 4.0, "x": 4.0, "mix_sec": 8.0, "rate": 1.0})
    assert abs(same) < 0.006


@needs_audio
def test_a_bridge_is_made_from_two_files_and_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(djmix, "BRIDGE_DIR", tmp_path / "bridges")
    write_song(tmp_path / "a.wav", 122, 70, note=220)
    write_song(tmp_path / "b.wav", 126, 70, note=247)
    ia, ib = bpm.analyze(tmp_path / "a.wav"), bpm.analyze(tmp_path / "b.wav")
    assert ia and ib and ia["conf"] > 0.5
    res = djmix.make("local:a", "local:b", tmp_path / "a.wav", tmp_path / "b.wav", ia, ib, 70.0, "blend")
    assert not res["skip"] and res["style"] == "blend" and res["lock"] and abs(res["align_ms"]) <= djmix.MAX_ALIGN_MS
    f = djmix.bridge_file(res["key"])
    assert f and f.suffix == ".m4a" and f.stat().st_size > 20000
    # the bridge as the player will hear it: as long as it says, and its first seconds are the old song's file
    got = djmix._decode(f, 0, 100)
    assert len(got) / djmix.SR == pytest.approx(res["duration"], abs=0.15)
    a0 = djmix._decode(tmp_path / "a.wav", res["a_start"], djmix.LEAD)
    n = min(len(a0), int(djmix.LEAD * djmix.SR)) - 4000
    assert np.corrcoef(a0[2000:2000 + n, 0], got[2000:2000 + n, 0])[0, 1] > 0.97
    assert res["b_resume"] > res["x"] and res["b_resume"] < 70
    stamp = f.stat().st_mtime_ns
    again = djmix.make("local:a", "local:b", tmp_path / "a.wav", tmp_path / "b.wav", ia, ib, 70.0, "blend")
    assert again["key"] == res["key"] and f.stat().st_mtime_ns >= stamp and len(list((tmp_path / "bridges").glob("*.m4a"))) == 1  # nothing made twice
    other = djmix.make("local:a", "local:b", tmp_path / "a.wav", tmp_path / "b.wav", ia, ib, 70.0, "echo")
    assert other["key"] != res["key"]


def test_old_bridges_make_room(tmp_path, monkeypatch):
    monkeypatch.setattr(djmix, "BRIDGE_DIR", tmp_path)
    monkeypatch.setattr(djmix, "MAX_BYTES", 2500)
    import os
    for i in range(4):
        f = tmp_path / f"{i:040x}.m4a"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (1000 + i, 1000 + i))
    djmix._prune()
    left = sorted(p.name for p in tmp_path.glob("*.m4a"))
    assert left == [f"{2:040x}.m4a", f"{3:040x}.m4a"]  # the oldest went first
    assert djmix.bridge_file("../x") is None and djmix.bridge_file("0" * 40) is None


# ---------- The routes ----------

@pytest.fixture()
def clicks(tmp_path, monkeypatch, alice):
    from mutagen.id3 import TALB, TCON, TIT2, TPE1
    from mutagen.wave import WAVE
    music = tmp_path / "music"
    monkeypatch.setattr(main.library, "MUSIC_DIR", music)
    monkeypatch.setattr(main.library, "IMPORT_DIR", tmp_path / "imported")
    monkeypatch.setattr(main.library, "COVER_DIR", tmp_path / "covers")
    monkeypatch.setattr(djmix, "BRIDGE_DIR", tmp_path / "bridges")
    music.mkdir()
    for title, tempo, note in (("Click A", 122, 220), ("Click B", 126, 247)):
        p = music / f"{title}.wav"
        write_song(p, tempo, 70, note)
        f = WAVE(str(p))
        f.add_tags()
        for frame, val in ((TIT2, title), (TPE1, "Dev"), (TALB, "Clicks"), (TCON, "Electronic")):
            f.tags.add(frame(encoding=3, text=val))
        f.save()
    main.library.scan(full=True)
    ids = {t["title"]: t["id"] for t in alice.get("/api/search", params={"q": "Click"}).json()["items"]}
    return alice, ids


@needs_audio
def test_the_route_makes_a_bridge_and_serves_it(clicks):
    c, ids = clicks
    r = c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click B"], "style": "filter"}).json()
    assert not r["skip"] and r["style"] == "filter" and r["url"].startswith("/api/dj/bridge/") and r["url"].endswith(".m4a")
    assert {"a_start", "t_a", "mix_sec", "rate", "b_resume", "duration", "lead", "tail", "info", "align_ms"} <= set(r)
    f = c.get(r["url"])
    assert f.status_code == 200 and f.headers["content-type"] == "audio/mp4" and len(f.content) > 20000
    assert c.get(r["url"], headers={"Range": "bytes=0-99"}).status_code == 206  # seeking works
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click B"], "style": "filter"}).json()["key"] == r["key"]
    assert "dj_bridge" in c.get("/api/server-info").json()["functions"]


@needs_audio
def test_the_route_skips_what_cannot_be_mixed(clicks):
    c, ids = clicks
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click B"], "style": "fade"}).json()["skip"]  # the player does that itself
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click A"]}).json()["skip"]
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": "yt:abcdefghijk"}).json()["skip"]  # a plugin song has no file here
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": "local:00000000-0000-0000-0000-000000000000"}).status_code == 404
    assert c.get("/api/dj/bridge", params={"a": ids["Click A"], "b": ids["Click B"], "style": "nonsense"}).status_code == 422


def test_bridges_need_a_login(anon):
    assert anon.get("/api/dj/bridge", params={"a": "local:a", "b": "local:b"}).status_code == 401
    assert anon.get("/api/dj/bridge/" + "0" * 40 + ".m4a").status_code == 401


def test_a_missing_bridge_is_a_404(alice):
    assert alice.get("/api/dj/bridge/" + "0" * 40 + ".m4a").status_code == 404 and alice.get("/api/dj/bridge/../../x.m4a").status_code in (404, 422)
    assert make_client("Bob").get("/api/dj/bridge/" + "1" * 40 + ".m4a").status_code == 404


def test_the_loop_roll_repeats_ever_shorter_pieces_of_the_old_song():
    a, _ = _pieces()
    a = a * np.linspace(0.2, 1, len(a))[:, None].astype(np.float32)  # not periodic, so that repeats can be told from the original
    i0, beat = int(2.0 * djmix.SR), 0.5
    out = djmix._roll(a, i0, beat)
    n = int(beat * djmix.SR)
    assert np.array_equal(out[:i0], a[:i0]) and np.array_equal(out[i0 + 4 * n:], a[i0 + 4 * n:])  # only the four beats change
    assert np.allclose(out[i0 + 40:i0 + n - 40], out[i0 + n + 40:i0 + 2 * n - 40], atol=1e-6)  # the first beat is played twice
    tail = i0 + 3 * n + n // 2
    assert np.allclose(out[tail + 40:tail + n // 8 - 40], out[tail + n // 8 + 40:tail + n // 4 - 40], atol=1e-6)  # and the last piece four times


def test_without_a_tempo_match_the_styles_that_do_not_need_one_are_chosen():
    import random
    got = {djmix.pick_style(False, 0, False, "auto", "", random.Random(i)) for i in range(60)}
    assert got == {"echo", "cut", "reverb"}
    assert djmix.pick_style(False, 0, False, "loop") == "echo" and djmix.pick_style(False, 0, False, "cut") == "cut"
    p = djmix.plan(info(120.0), info(90.0), 240.0, "cut")
    assert not p["skip"] and not p["lock"] and p["style"] == "cut" and p["info"]["mb"] == 4


def test_a_quiet_start_is_brought_up_only_while_the_transition_is_on():
    a, b = _pieces()
    n = int(round((djmix.LEAD + 8.0 + djmix.TAIL) * djmix.SR))
    ax, bx = np.zeros((n, 2), np.float32), np.zeros((n, 2), np.float32)
    ax[:len(a)] = a[:n]
    bx[int(djmix.LEAD * djmix.SR):] = (b * 0.02)[: n - int(djmix.LEAD * djmix.SR)]
    g = djmix._level_gain(ax, bx, 8.0, n)
    at = lambda s: float(g[int((djmix.LEAD + s) * djmix.SR)])  # noqa: E731
    assert at(1.0) == pytest.approx(2.0, abs=0.01)  # up by 6 dB, not more
    assert at(-2.0) == pytest.approx(g[0]) and 1.0 < at(8 + 1.5) < 2.0 and at(8 + 3.6) == 1.0 and g[-1] == 1.0  # and gone when the player hands over
    loud = djmix._level_gain(ax, ax * 2, 8.0, n)
    assert np.allclose(loud, 1.0)  # a new song that is loud enough is left alone


def test_the_room_of_the_cut_rings_on_after_the_old_song_stopped():
    a, b = _pieces()
    quiet_b = np.zeros_like(b)
    out = djmix.mix_pieces(a, quiet_b, {**PLAN, "style": "cut"})
    lead = int(djmix.LEAD * djmix.SR)
    after = np.abs(out[lead + int(0.05 * djmix.SR): lead + int(0.3 * djmix.SR)]).max()
    assert 0.001 < after < np.abs(a[:lead]).max()  # something is there, softer than the song
    assert np.abs(out[lead + int(1.6 * djmix.SR):]).max() < 1e-3  # and it dies away
