"""DJ bridges: the stretch of music in which one song turns into the next, made on the server.

The player used to line two songs up itself, with two audio elements, which cannot be more exact than some tens of milliseconds. Here the server cuts the
end of the old song and the beginning of the new one out of the files, lines them up (on the beat grid from the analysis, then corrected by comparing the
drum onsets of the two pieces), stretches the new song to the old one's tempo, applies the effect of the style (bass swap, filter sweep, echo) and returns the
result as one short file, the bridge.

The player plays the old song as usual. A few seconds before the transition it fades over to the bridge (which begins with the old song, so it is the same
music), and a few seconds after the transition it fades over to the new song's own file (the bridge ends with the new song). Play, pause, skip and seeking
work on the bridge like on any file.

Needs numpy and ffmpeg, like the tempo analysis. `plan` and `align_offset` need nothing else and are tested without audio.
"""
import hashlib
import json
import logging
import math
import os
import random
import re
import subprocess
import threading
from pathlib import Path

import bpm as bpmlib
import db
import stems

try:
    import numpy as np
except ImportError:  # no bridges then
    np = None

log = logging.getLogger("djmix")

SR = 44100
LEAD = 4.0            # seconds of the old song before the transition that the bridge carries (the player fades over to the bridge here)
TAIL = 10.0           # seconds of the new song after the transition (the player fades over to the new song's own file in here)
MAX_LOCK = 0.08       # the new song is stretched to the old one's tempo if they differ by no more than this
MIN_CONF = 0.35       # the analysis must be this sure
MAX_ALIGN_MS = 80     # the correction found by comparing drum onsets is accepted up to this
MIN_ALIGN_STRENGTH = 0.15
RENDER_VERSION = 3    # raise when the sound of a bridge changes: old files are not reused
BRIDGE_DIR = Path(os.getenv("DJ_BRIDGE_DIR") or Path(db.DB_PATH).parent / "dj-bridges")  # next to the database, in the data volume
MAX_BYTES = int(float(os.getenv("DJ_BRIDGE_MB", "300")) * 1024 * 1024)
ENABLED = os.getenv("DJ_BRIDGES", "1") != "0"

_render_lock = threading.Semaphore(1)  # one bridge at a time: it takes a second or two of the processor


def available() -> bool:
    return ENABLED and np is not None and bpmlib.available()


# ---------- Planning (no audio needed) ----------

def key_fit(a: dict, b: dict) -> int:
    """Camelot wheel: same number or neighbour = harmonic. 1 fits, 0 unclear, -1 sounds off."""
    p, q = (re.fullmatch(r"(\d+)([AB])", x.get("key") or "") for x in (a, b))
    if not p or not q or (a.get("key_conf") or 0) < MIN_CONF or (b.get("key_conf") or 0) < MIN_CONF:
        return 0
    n = abs(int(p[1]) - int(q[1]))
    d = min(n, 12 - n) + (0 if p[2] == q[2] else (0 if n == 0 else 1))
    return 1 if d <= 1 else 0 if d <= 2 else -1


STYLES = ("blend", "filter", "echo", "loop", "bass", "cut", "reverb")
NO_LOCK_STYLES = ("echo", "cut", "reverb")  # these work without matching the tempo


def pick_style(lock: bool, fit: int, hot: bool, style: str = "auto", avoid: str = "", rng=random) -> str:
    """The style of a transition. Without tempo match only the echo works; otherwise the one asked for, or at random (never one of the last two:
    `avoid` is a comma separated list)."""
    if style in STYLES:
        return style if lock or style in NO_LOCK_STYLES else "echo"
    pool = [("blend", 0 if fit < 0 or hot else 4), ("filter", 2), ("echo", 5 if hot or fit < 0 else 2), ("loop", 4), ("bass", 0 if fit < 0 or hot else 4),
            ("cut", 4 if hot else 3), ("reverb", 2 if hot else 3)]
    if not lock:
        pool = [(k, w) for k, w in pool if k in NO_LOCK_STYLES]
    recent = avoid.split(",")
    pool = [(k, w) for k, w in pool if w > 0 and k not in recent]
    r = rng.random() * sum(w for _, w in pool)
    for k, w in pool:
        r -= w
        if r < 0:
            return k
    return pool[0][0] if pool else "echo"


def plan(a: dict, b: dict, a_dur: float, style: str = "auto", avoid: str = "", rng=random) -> dict:
    """What the transition from song A (analysis `a`, length `a_dur` s) into song B (analysis `b`) looks like.
    -> {"skip": True, "why"} or the plan: times in A and B, the stretch, the style and the facts behind the choices."""
    for name, x in (("old", a), ("new", b)):
        if not x or (x.get("conf") or 0) < MIN_CONF or (x.get("bpm") or 0) <= 0:
            return {"skip": True, "why": f"the {name} song has no usable tempo analysis"}
    bb = b["bpm"]
    for c in (b["bpm"] * 2, b["bpm"] / 2):  # bring B's tempo to the same octave as A (70 instead of 140)
        if abs(math.log(c / a["bpm"])) < abs(math.log(bb / a["bpm"])):
            bb = c
    rate = a["bpm"] / bb
    lock = abs(rate - 1) <= MAX_LOCK
    if not lock:
        rate = 1.0
    beat_a, beat_b = 60 / a["bpm"], 60 / bb
    bars = bool(a.get("bar_ok") and b.get("bar_ok") and a.get("first_bar") is not None and b.get("first_bar") is not None)
    g = 4 if bars else 1  # align to the downbeat ("the one") if both songs know it for sure, otherwise to single beats
    f_a = (a["first_bar"] if bars else a.get("first_beat")) or 0.0
    f_b = (b["first_bar"] if bars else b.get("first_beat")) or 0.0
    x = f_b % (g * beat_b)  # the new song comes in at the first point of its grid: nearly from its very beginning
    end_a = min(a_dur, a.get("audio_end") or a_dur) - 0.8
    outro = a.get("outro_start")
    has_outro = outro is not None and end_a - outro >= 8 * beat_a
    intro = b.get("intro_end")
    intro_beats = None if intro is None else (intro - x) / beat_b
    hot = intro_beats is not None and intro_beats < 4  # the new song is loud at once: a short transition without bass swap
    fit = key_fit(a, b)
    st = pick_style(lock, fit, hot, style, avoid, rng)

    def q4(v: float) -> int:
        return max(8, min(32, int(v // 4) * 4))

    mb = 8 if st == "echo" else 4 if st in ("cut", "reverb") else 16  # length in beats: as long as A's outro and B's intro allow
    if st not in ("echo", "cut", "reverb"):
        if has_outro:
            mb = q4((end_a - outro) / beat_a)
        if intro_beats is not None and intro_beats >= 8:
            mb = min(mb, q4(intro_beats))
        if fit < 0 or hot:
            mb = min(mb, 8)
    mix = mb * beat_a
    swap = max(4, int(0.75 * mb // 4) * 4)  # "bass": the bass moves over on a bar line, at about three quarters
    g_a = g * beat_a
    latest = end_a - mix

    def on_grid(t: float, up: bool) -> float:
        return f_a + (math.ceil((t - f_a) / g_a - 1e-6) if up else math.floor((t - f_a) / g_a)) * g_a

    t_a = on_grid(outro, True) if has_outro else on_grid(latest, False)
    if t_a > latest:
        t_a = on_grid(latest, False)
    if t_a > latest + 0.01 or t_a - LEAD < 0:
        return {"skip": True, "why": "the song ends before a transition on the beat fits"}
    r3 = lambda v: round(float(v), 3)  # noqa: E731
    return {"skip": False, "style": st, "lock": lock, "rate": round(rate, 5), "bpm_a": r3(a["bpm"]), "bpm_b": r3(bb), "beat_a": r3(beat_a),
            "t_a": r3(t_a), "mix_sec": r3(mix), "x": r3(x), "a_start": r3(t_a - LEAD), "lead": LEAD, "tail": TAIL,
            "info": {"fit": fit, "hot": hot, "has_outro": has_outro, "bars": bars, "mb": mb},
            "swap": swap, "gap": st == "bass" and rng.random() < 0.5}


# ---------- Lining up ----------

def align_offset(env_a, env_b, max_frames: int = 7) -> tuple[float, float]:
    """How many frames the beats of B come after those of A (negative: before), from the onset curves of the two pieces, and how clear that is (0..1).
    A parabola through the peak gives a fraction of a frame."""
    n = min(len(env_a), len(env_b))
    if n < 2 * max_frames + 8:
        return 0.0, 0.0
    ea, eb = env_a[:n] - env_a[:n].mean(), env_b[:n] - env_b[:n].mean()
    norm = float(np.sqrt((ea ** 2).mean() * (eb ** 2).mean())) + 1e-9
    vals = {}
    for k in range(-max_frames, max_frames + 1):
        vals[k] = float(np.dot(ea[: n - k], eb[k:]) if k >= 0 else np.dot(ea[-k:], eb[: n + k])) / (n - abs(k)) / norm
    k0 = max(vals, key=vals.get)
    frac = 0.0
    if -max_frames < k0 < max_frames:
        y0, y1, y2 = vals[k0 - 1], vals[k0], vals[k0 + 1]
        d = y0 - 2 * y1 + y2
        if d < 0:
            frac = 0.5 * (y0 - y2) / d
    return k0 + frac, max(0.0, min(1.0, vals[k0]))


# ---------- Audio ----------

def _decode(path: Path, start: float, seconds: float, rate: float = 1.0, sr: int = SR, channels: int = 2):
    """`seconds` of the file from `start`, as float32; `rate` changes the speed without changing the pitch (the result is that much shorter or longer)."""
    cmd = ["ffmpeg", "-v", "error"]
    if start > 0.001:  # not at 0: "-ss 0" skips the encoder's start-up samples of an m4a file twice (1024 samples off)
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-t", f"{seconds:.4f}", "-i", str(path)]
    if abs(rate - 1) > 1e-4:
        cmd += ["-af", f"atempo={rate:.5f}"]
    cmd += ["-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=90, check=True).stdout, dtype=np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def _measure_offset(a_path: Path, b_path: Path, p: dict) -> tuple[float, float]:
    """How much later than A's beats B's beats fall (seconds; positive: B has to come that much earlier), and how sure that is (0..1).
    Compares the bass onsets (and the whole spectrum's) of the transition in A with those of B stretched like in the bridge."""
    sr, mix = 11025, p["mix_sec"]
    xa = _decode(a_path, p["t_a"], mix, 1.0, sr, 1)
    xb = _decode(b_path, p["x"], mix * p["rate"], p["rate"], sr, 1)
    best = (0.0, 0.0)
    for low in (True, False):
        ea, eb = bpmlib._onset_envelope(xa, low=low), bpmlib._onset_envelope(xb, low=low)
        if len(ea) < 40 or len(eb) < 40:
            continue
        frames, strength = align_offset(ea, eb)
        if strength > best[1]:
            best = (frames * bpmlib.HOP / sr, strength)
    shift, strength = best
    if strength < MIN_ALIGN_STRENGTH or abs(shift) * 1000 > MAX_ALIGN_MS:
        return 0.0, strength  # no clear beat to go by, or a jump that is more likely a mistake than a correction
    return shift, strength


def _sweep(x, cutoff, kind: str, n_fft: int = 2048, hop: int = 512):
    """A high-pass ("hp") or low-pass ("lp") whose cutoff (Hz, one value per sample) moves, by shaping the spectrum frame by frame (no phase shift)."""
    n = len(x)
    win = np.hanning(n_fft).astype(np.float32)
    pad = n_fft
    xp = np.pad(x, ((pad, pad + n_fft), (0, 0)))
    out, norm = np.zeros_like(xp), np.zeros(len(xp), dtype=np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)[:, None]
    for start in range(0, len(xp) - n_fft, hop):
        fc = float(cutoff[min(max(start + n_fft // 2 - pad, 0), n - 1)])
        r = (freqs / fc) ** 2
        mask = (r / np.sqrt(1 + r ** 2)) if kind == "hp" else (1 / np.sqrt(1 + r ** 2))  # a 2nd-order Butterworth's magnitude
        spec = np.fft.rfft(xp[start:start + n_fft] * win[:, None], axis=0) * mask
        out[start:start + n_fft] += np.fft.irfft(spec, n_fft, axis=0).astype(np.float32) * win[:, None]
        norm[start:start + n_fft] += win ** 2
    return (out / np.maximum(norm, 1e-6)[:, None])[pad:pad + n]


def _level_gain(ax, bx, mix: float, n: int):
    """A quiet start of the new song (a long intro) is brought up towards the level of the old song, by at most 6 dB, and only while the transition is on:
    the gain is back to 1 3.5 s after it, before the player hands over to the new song's own file, which has no such gain."""
    win = SR // 2
    ref = float(np.sqrt(np.mean(ax[int((LEAD - 4.0) * SR):int(LEAD * SR)].mean(axis=1) ** 2))) + 1e-6
    target = 0.7 * ref
    starts = np.arange(int(LEAD * SR), min(n, int((LEAD + mix + 3.5) * SR)), win)
    if len(starts) < 2:
        return np.ones(n, dtype=np.float32)
    lev = np.array([np.sqrt(np.mean(bx[s:s + win].mean(axis=1) ** 2)) for s in starts])
    want = np.clip(target / (lev + 1e-4), 1.0, 2.0)
    want = np.convolve(np.pad(want, 2, mode="edge"), np.ones(5) / 5, mode="valid")  # smooth: no pumping
    tau = (starts + win / 2 - LEAD * SR) / SR
    fade = np.clip(1.0 - (tau - mix) / 3.5, 0.0, 1.0)
    g = 1.0 + (want - 1.0) * fade
    t = np.arange(n) / SR - LEAD
    return np.interp(t, tau, g, left=g[0], right=1.0).astype(np.float32)


def _reverb(x, tau: float, seconds: float, seed: int = 7):
    """The sound `x` (stereo) through a room: noise that dies away (time constant `tau`, `seconds` long), different for each ear. The tail is longer than `x`."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    env = np.exp(-np.arange(n) / (tau * SR)).astype(np.float32)
    ir = (rng.standard_normal((n, 2)).astype(np.float32) * env[:, None])
    ir[: int(0.004 * SR)] *= np.linspace(0, 1, int(0.004 * SR))[:, None]  # no click at the start
    ir /= np.sqrt((ir ** 2).sum(axis=0)) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size, axis=0) * np.fft.rfft(ir, size, axis=0), size, axis=0)[: len(x) + n]
    return y.astype(np.float32)


def _roll(ax, i0: int, beat: float):
    """`ax` where the four beats from sample `i0` are replaced by loops that get shorter: each piece is the start of its part of the music, repeated."""
    out = ax.copy()
    pos = i0
    for length, reps in ((1.0, 2), (0.5, 2), (0.25, 2), (0.125, 4)):
        n = int(round(length * beat * SR))
        piece = ax[pos:pos + n].copy()
        if len(piece) < n:
            piece = np.pad(piece, ((0, n - len(piece)), (0, 0)))
        ramp = min(n // 4, int(0.003 * SR))  # 3 ms at the seams: no clicks
        if ramp > 1:
            piece[:ramp] *= np.linspace(0, 1, ramp)[:, None]
            piece[-ramp:] *= np.linspace(1, 0, ramp)[:, None]
        for k in range(reps):
            a0 = pos + k * n
            if a0 + n <= len(out):
                out[a0:a0 + n] = piece
        pos += reps * n
    return out


def _up(x):
    return np.sin(np.pi / 2 * np.clip(x, 0.0, 1.0))


def _down(x):
    return np.cos(np.pi / 2 * np.clip(x, 0.0, 1.0))


def _ramp(tau, start: float, length: float, a: float, b: float, exp: bool = False):
    """Value that is `a` until `start`, moves to `b` over `length` seconds (straight, or exponentially for frequencies) and stays there."""
    f = np.clip((tau - start) / max(length, 1e-6), 0.0, 1.0)
    return a * (b / a) ** f if exp else a + (b - a) * f


def _vocal_ride(va, vb, mix: float, beat: float, n: int, report: dict | None = None):
    """What the singing does while the music changes (`va`, `vb`: the vocal tracks of the old and the new song, placed on the bridge's clock).
    The old singer is let finish: until the first pause of 0.35 s or more in the transition (or, without one, until a beat before its end), then it stops
    with a short reverb. The new singer is kept out until then, and comes in at the first start of a phrase after that (or at once if it sings without a break).
    -> the singing to add to the bridge, from the transition on."""
    hop = int(0.05 * SR)
    frames = n // hop

    def envelope(v):
        k = min(frames, len(v) // hop)
        e = np.zeros(frames)
        e[:k] = np.sqrt((v[:k * hop].mean(axis=1).reshape(k, hop) ** 2).mean(axis=1))
        return e

    def active(e, hi_frame):
        loud = e[:hi_frame]
        thr = max(0.15 * float(np.percentile(loud[loud > 0], 95)) if (loud > 0).any() else 0.0, 0.003)
        return e > thr

    f0, f1 = int(LEAD / 0.05), int((LEAD + mix) / 0.05)
    act_a, act_b = active(envelope(va), f1), active(envelope(vb), min(frames, f1 + int(TAIL / 0.05)))
    gap = 7  # frames of silence that count as a pause (0.35 s)
    stop_f = None
    for f in range(f0, max(f0, f1 - int(0.5 * beat / 0.05))):
        if not act_a[f:f + gap].any():
            stop_f = f
            break
    t_stop = LEAD + mix - beat if stop_f is None else stop_f * 0.05
    fade_out = beat if stop_f is None else 0.04
    # the new singer: the first start of a phrase after the old one stopped, but not later than 3 s after the transition
    t_in, latest = None, LEAD + mix + 3.0
    for f in range(max(1, int(t_stop / 0.05)), min(frames, int(latest / 0.05))):
        if act_b[f] and not act_b[max(0, f - gap):f].any():
            t_in = f * 0.05 - 0.03
            break
    fade_in = 0.04
    if t_in is None:
        t_in, fade_in = t_stop, 0.08  # it sings without a break: it comes in where the old one stopped
    t = np.arange(n) / SR
    g_a = np.where(t < t_stop, 1.0, np.clip(1.0 - (t - t_stop) / fade_out, 0.0, 1.0))
    g_a = np.where(t < LEAD, 0.0, g_a)  # until the transition the bridge has the old song as it is, with its singing
    g_b = np.clip((t - t_in) / fade_in, 0.0, 1.0)
    out = va * g_a[:, None] + vb * g_b[:, None]
    k1 = int(t_stop * SR)
    k0 = max(0, k1 - int(0.3 * SR))
    ring = _reverb(va[k0:k1] * np.linspace(0.3, 1.0, k1 - k0)[:, None].astype(np.float32), 0.35, 1.6) * 0.5
    ring = _sweep(np.pad(ring, ((0, SR // 8), (0, 0))), np.full(len(ring) + SR // 8, 200.0), "hp")[: len(ring)]
    ring = ring[k1 - k0:]
    k = min(len(ring), n - k1)
    if k > 0:
        out[k1:k1 + k] += ring[:k]
    if report is not None:
        both = act_a[:frames] & act_b[:frames]
        win = slice(f0, f1)
        heard = both & (g_a[::hop][:frames] > 0.5) & (g_b[::hop][:frames] > 0.5)
        report.update(a_stop=round(t_stop - LEAD, 2), a_pause=stop_f is not None, b_in=round(t_in - LEAD, 2), b_phrase=fade_in == 0.04,
                      overlap_s=round(float(heard[win].sum()) * 0.05, 2), would_overlap_s=round(float(both[win].sum()) * 0.05, 2))
    return out


def mix_pieces(a, b_piece, p: dict, delta: float = 0.0, vocals=None, report: dict | None = None):
    """The bridge as float32 stereo: `a` is the old song from `a_start` (at least LEAD + mix seconds), `b_piece` the new one from `x`, already stretched.
    The first LEAD seconds are the old song untouched; the effect starts with the transition, `delta` s later or earlier for the new song.
    `vocals` (optional): the vocal tracks of `a` and `b_piece`. Then the effect works on the music without the singing and the singing is led on its own."""
    mix, beat, st = p["mix_sec"], p["beat_a"], p["style"]
    total = LEAD + mix + TAIL
    n = int(round(total * SR))
    t = np.arange(n) / SR
    tau = t - LEAD  # seconds since the transition began
    ax = np.zeros((n, 2), dtype=np.float32)
    m = min(len(a), n)
    ax[:m] = a[:m]
    ax[int((LEAD + mix) * SR):] = 0  # nothing of the old song after the transition (its echo is added below)
    bx = np.zeros((n, 2), dtype=np.float32)
    off = int(round((LEAD + delta) * SR))
    if off >= 0:
        k = min(len(b_piece), n - off)
        if k > 0:
            bx[off:off + k] = b_piece[:k]
    else:
        k = min(len(b_piece) + off, n)
        bx[:k] = b_piece[-off:-off + k]
    va = vb = None
    if vocals is not None:
        va = np.zeros_like(ax)
        va[:m] = vocals[0][:m]
        va[int((LEAD + mix) * SR):] = 0
        vb = np.zeros_like(bx)
        if off >= 0:
            if k > 0:
                vb[off:off + k] = vocals[1][:k]
        else:
            vb[:k] = vocals[1][-off:-off + k]
    if p.get("level"):
        g = _level_gain(ax, bx, mix, n)[:, None]
        bx *= g
        if vb is not None:
            vb *= g
    ax_full = ax
    if va is not None:
        ax, bx = ax - va, bx - vb  # the music without the singing (what the model did not catch stays in it, so that both add up to the song again)
    s0 = max(0, int((LEAD - 0.3) * SR))  # the effects begin here; before it the old song stays as it is
    neutral_hp, neutral_lp = np.full(n, 10.0), np.full(n, 20000.0)

    if st == "fade":
        ga, gb = np.where(tau < 0, 1.0, _down(tau / mix)), np.where(tau < 0, 0.0, _up(tau / mix))
        hp_a, hp_b, lp_a = neutral_hp, neutral_hp, neutral_lp
        echo_send = None
    elif st == "blend":  # swap the bass: B comes in without bass, in the middle the bass moves over from A to B
        ga, gb = np.where(tau < 0, 1.0, _down(tau / mix)), np.where(tau < 0, 0.0, _up(tau / mix))
        hp_b = _ramp(tau, mix / 2, 0.4, 450.0, 10.0)
        hp_a = _ramp(tau, mix / 2, 0.4, 10.0, 450.0)
        lp_a, echo_send = neutral_lp, None
    elif st == "filter":  # A gets dull and quiet, B opens up
        lp_a = _ramp(tau, 0, 0.9 * mix, 20000.0, 240.0, exp=True)
        ga = np.where(tau < mix / 2, 1.0, np.clip(1 - (tau - mix / 2) / (mix / 2), 0.0, 1.0))
        hp_b = _ramp(tau, 0, 0.7 * mix, 900.0, 10.0, exp=True)
        gb = np.where(tau < 0, 0.0, _up(tau / (0.4 * mix)))
        hp_a, echo_send = neutral_hp, None
    elif st == "loop":  # A's last four beats are looped ever shorter (1/4, 1/8, 1/16, 1/32 bar), then the cut: B's bass takes over while B was only mids so far
        roll = max(0.0, mix - 4 * beat)
        ga = np.where(tau < mix, 1.0, 0.0)
        hp_a = _ramp(tau, roll, 4 * beat, 10.0, 1500.0, exp=True)
        hp_b = _ramp(tau, mix, 0.03, 450.0, 10.0)
        gb = np.where(tau < mix, 0.9 * _up(tau / (0.6 * mix)), 1.0)
        lp_a, echo_send = neutral_lp, None
    elif st == "bass":  # A keeps only its bass, B comes in above it without bass; on the bar line the bass switches to B (before it, maybe a beat without any)
        sw = p.get("swap", mix * 0.75)
        lp_a = _ramp(tau, 0, 0.5 * sw, 20000.0, 150.0, exp=True)
        if p.get("gap"):
            lp_a = np.minimum(lp_a, _ramp(tau, sw - beat, 0.08, 20000.0, 25.0, exp=True))
        ga = np.where(tau < sw, 1.0, 0.0)
        hp_b = _ramp(tau, sw, 0.03, 450.0, 10.0)
        gb = np.where(tau < 0, 0.0, _up(tau / (0.5 * sw)))
        hp_a, echo_send = neutral_hp, None
    elif st == "cut":  # A stops dead on the beat and rings out in a small room; B starts at once
        ga = np.where(tau < 0, 1.0, np.clip(1 - tau / 0.012, 0.0, 1.0))
        hp_b = neutral_hp
        gb = np.where(tau < 0, 0.0, _up(tau / 0.02))
        hp_a, lp_a, echo_send = neutral_hp, neutral_lp, None
    elif st == "reverb":  # A is washed away into a big room while it fades out, B comes in without bass underneath
        ga = np.where(tau < 0, 1.0, _down(tau / (1.5 * beat)))
        hp_b = _ramp(tau, 1.5 * beat, 2 * beat, 350.0, 10.0, exp=True)
        gb = np.where(tau < 0, 0.0, _up((tau - 0.5 * beat) / (2 * beat)))
        hp_a, lp_a, echo_send = neutral_hp, neutral_lp, None
    else:  # echo: A fades out into an echo, B comes in on the beat
        ga = np.where(tau < 0, 1.0, np.clip(1 - tau / (0.5 * beat), 0.0, 1.0))
        hp_b = _ramp(tau, 0, 2 * beat, 900.0, 10.0, exp=True)
        gb = np.where(tau < 0, 0.0, _up(tau / (1.5 * beat)))
        hp_a, lp_a = neutral_hp, neutral_lp
        echo_send = np.where((tau >= 0) & (tau <= beat), 0.9 * (1 - tau / beat), 0.0)
    ga = np.where(tau > mix, 0.0, ga)
    wet = None
    if st in ("cut", "reverb"):  # what A leaves in the room: its last stretch (a quarter beat for the cut, a whole beat for the wash) goes through the reverb
        k0 = int((LEAD - (0.25 if st == "cut" else 1.0) * beat) * SR)
        k1 = int(LEAD * SR)
        tail = ax_full[k0:k1] * np.linspace(0.3, 1.0, k1 - k0)[:, None].astype(np.float32)
        wet = _reverb(tail, 0.25 if st == "cut" else 0.7, 1.2 if st == "cut" else 4.0) * (0.55 if st == "cut" else 0.8)
        wet = _sweep(np.pad(wet, ((0, SR // 8), (0, 0))), np.full(len(wet) + SR // 8, 180.0), "hp")[: len(wet)]  # no mud in the bass

    a_fx = ax.copy()
    if st in ("blend",):
        a_fx[s0:] = _sweep(a_fx[s0:], hp_a[s0:], "hp")
    if st in ("filter", "bass"):
        a_fx[s0:] = _sweep(a_fx[s0:], lp_a[s0:], "lp")
    if st == "loop":
        i0 = int(round((LEAD + max(0.0, mix - 4 * beat)) * SR))
        a_fx[i0:] = _sweep(_roll(ax, i0, beat)[i0:], hp_a[i0:], "hp")
    a_out = np.where((tau < 0)[:, None], ax_full, a_fx * ga[:, None])  # identical to the old song until the transition begins
    if echo_send is not None:
        src = ax_full * echo_send[:, None]
        delay = int(round(0.75 * beat * SR))
        for k in range(1, 7):  # a few repeats, each softer
            sh = k * delay
            if sh < n:
                a_out[sh:] += 0.6 ** (k - 1) * src[: n - sh]
    b_fx = _sweep(bx[s0:], hp_b[s0:], "hp") if st != "fade" else bx[s0:]
    b_out = np.zeros_like(bx)
    b_out[s0:] = b_fx * gb[s0:, None]
    out = a_out + b_out
    if va is not None:
        out += _vocal_ride(va, vb, mix, beat, n, report)
    if wet is not None:
        w0 = int(LEAD * SR)  # the room rings on from where A is cut or starts to fade; what the reverb made before that is already part of A
        ring = wet[w0 - k0:]
        n_w = min(len(ring), n - w0)
        out[w0:w0 + n_w] += ring[:n_w]
    over = np.abs(out) > 0.95  # a gentle limit, only where the sum would be too loud
    out = np.where(over, np.sign(out) * (0.95 + 0.05 * np.tanh((np.abs(out) - 0.95) / 0.05)), out)
    return out.astype(np.float32)


def _encode(pcm, dest: Path) -> None:
    tmp = dest.with_name(dest.stem + ".tmp.m4a")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    subprocess.run(cmd, input=pcm.astype("<f4").tobytes(), check=True, timeout=90, capture_output=True)
    os.replace(tmp, dest)


def _separate(x, lo: float, hi: float):
    """The singing of `x` between the seconds `lo` and `hi`, silence elsewhere (the model needs time to run: the less it is given, the better)."""
    out = np.zeros_like(x)
    i0, i1 = max(0, int(lo * SR)), min(len(x), int(hi * SR))
    if i1 > i0:
        out[i0:i1] = stems.vocals(x[i0:i1])
    return out


def render(a_path: Path, b_path: Path, p: dict, dest: Path) -> dict:
    """Makes the bridge file for a plan. -> {"align_ms", "align_strength", "b_resume", "duration"}."""
    shift, strength = _measure_offset(a_path, b_path, p) if p["lock"] else (0.0, 0.0)  # beats of songs with different tempi do not line up: nothing to correct
    mix, rate = p["mix_sec"], p["rate"]
    a = _decode(a_path, p["a_start"], LEAD + mix + 0.5)
    b = _decode(b_path, p["x"], (mix + TAIL + 1.0) * rate, rate)  # stretched: (mix + TAIL + 1) seconds long
    vocals, report = None, {}
    if stems.available():
        try:
            # only what is heard of the singing: A from a second before the transition to its end, B until a few seconds after the transition
            # (the rest of both is the song as it is: where there is no separated singing, the music is the whole signal and nothing changes)
            vocals = (_separate(a, LEAD - 1.3, LEAD + mix + 0.5), _separate(b, 0.0, mix + 5.0))
        except Exception as e:  # no model, no memory: a bridge without it is still a bridge
            log.warning("stems: not used for this bridge: %s", e)
    pcm = mix_pieces(a, b, {**p, "level": True}, -shift, vocals, report)  # B's beats fall `shift` s late: it has to start that much earlier
    dest.parent.mkdir(parents=True, exist_ok=True)
    _encode(pcm, dest)
    total = len(pcm) / SR
    started = LEAD - shift  # where on the bridge's clock the new song's piece begins
    return {"align_ms": round(-shift * 1000, 1), "align_strength": round(strength, 2), "duration": round(total, 3), "vocals": report or None,
            "b_resume": round(p["x"] + rate * (total - started), 3)}


# ---------- Cache ----------

def _key(a_id: str, b_id: str, a_path: Path, b_path: Path, p: dict) -> str:
    def stat(x: Path) -> list:
        s = x.stat()
        return [s.st_size, int(s.st_mtime)]
    blob = json.dumps([RENDER_VERSION, bpmlib.ANALYSIS_VER, stems.available(), a_id, b_id, stat(a_path), stat(b_path),
                       {k: p[k] for k in ("style", "rate", "t_a", "mix_sec", "x", "beat_a", "swap", "gap")}], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()


def bridge_file(key: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{40}", key):
        return None
    f = BRIDGE_DIR / f"{key}.m4a"
    return f if f.is_file() else None


def _prune() -> None:
    """Keeps the folder under the size limit: the least recently used files go first."""
    if MAX_BYTES <= 0 or not BRIDGE_DIR.is_dir():
        return
    for side in BRIDGE_DIR.glob("*.json"):  # the notes of a bridge whose file is gone
        if not side.with_suffix(".m4a").exists():
            side.unlink(missing_ok=True)
    files = sorted((f for f in BRIDGE_DIR.glob("*.m4a") if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= MAX_BYTES:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def make(a_id: str, b_id: str, a_path: Path, b_path: Path, a_info: dict, b_info: dict, a_dur: float, style: str = "auto", avoid: str = "") -> dict:
    """The bridge from song A into song B: the plan with `key` (the file's name) and what the render found, or {"skip": True, "why"}.
    The same pair with the same style gives the same bridge: it is made once and kept."""
    if not available():
        return {"skip": True, "why": "bridges are turned off on this server"}
    if style == "fade":
        return {"skip": True, "why": "a plain crossfade is made by the player"}
    rng = random.Random(f"{a_id}>{b_id}")  # the same pair gets the same style every time, so that its bridge can be reused
    p = plan(a_info, b_info, a_dur, style, avoid, rng)
    if p["skip"]:
        return p
    key = _key(a_id, b_id, a_path, b_path, p)
    dest = BRIDGE_DIR / f"{key}.m4a"
    meta = BRIDGE_DIR / f"{key}.json"
    if dest.is_file() and meta.is_file():
        try:
            found = json.loads(meta.read_text(encoding="utf-8"))
            dest.touch()
            return {**p, **found, "key": key}
        except (OSError, ValueError):
            pass
    with _render_lock:
        if not (dest.is_file() and meta.is_file()):
            found = render(a_path, b_path, p, dest)
            meta.write_text(json.dumps(found), encoding="utf-8")
            _prune()
        else:
            found = json.loads(meta.read_text(encoding="utf-8"))
    return {**p, **found, "key": key}
