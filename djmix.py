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
RENDER_VERSION = 1    # raise when the sound of a bridge changes: old files are not reused
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


def pick_style(lock: bool, fit: int, hot: bool, style: str = "auto", avoid: str = "", rng=random) -> str:
    """The style of a transition. Without tempo match only the echo works; otherwise the one asked for, or at random (never the one before)."""
    if not lock:
        return "echo"
    if style in ("blend", "filter", "echo"):
        return style
    pool = [("blend", 0 if fit < 0 or hot else 5), ("filter", 3), ("echo", 5 if hot or fit < 0 else 2)]
    pool = [(k, w) for k, w in pool if w > 0 and k != avoid]
    r = rng.random() * sum(w for _, w in pool)
    for k, w in pool:
        r -= w
        if r < 0:
            return k
    return pool[0][0]


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

    mb = 8 if st == "echo" else 16  # length in beats: as long as A's outro and B's intro allow
    if st != "echo":
        if has_outro:
            mb = q4((end_a - outro) / beat_a)
        if intro_beats is not None and intro_beats >= 8:
            mb = min(mb, q4(intro_beats))
        if fit < 0 or hot:
            mb = min(mb, 8)
    mix = mb * beat_a
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
            "info": {"fit": fit, "hot": hot, "has_outro": has_outro, "bars": bars, "mb": mb}}


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


def _up(x):
    return np.sin(np.pi / 2 * np.clip(x, 0.0, 1.0))


def _down(x):
    return np.cos(np.pi / 2 * np.clip(x, 0.0, 1.0))


def _ramp(tau, start: float, length: float, a: float, b: float, exp: bool = False):
    """Value that is `a` until `start`, moves to `b` over `length` seconds (straight, or exponentially for frequencies) and stays there."""
    f = np.clip((tau - start) / max(length, 1e-6), 0.0, 1.0)
    return a * (b / a) ** f if exp else a + (b - a) * f


def mix_pieces(a, b_piece, p: dict, delta: float = 0.0):
    """The bridge as float32 stereo: `a` is the old song from `a_start` (at least LEAD + mix seconds), `b_piece` the new one from `x`, already stretched.
    The first LEAD seconds are the old song untouched; the effect starts with the transition, `delta` s later or earlier for the new song."""
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
    else:  # echo: A fades out into an echo, B comes in on the beat
        ga = np.where(tau < 0, 1.0, np.clip(1 - tau / (0.5 * beat), 0.0, 1.0))
        hp_b = _ramp(tau, 0, 2 * beat, 900.0, 10.0, exp=True)
        gb = np.where(tau < 0, 0.0, _up(tau / (1.5 * beat)))
        hp_a, lp_a = neutral_hp, neutral_lp
        echo_send = np.where((tau >= 0) & (tau <= beat), 0.9 * (1 - tau / beat), 0.0)
    ga = np.where(tau > mix, 0.0, ga)

    a_fx = ax.copy()
    if st in ("blend",):
        a_fx[s0:] = _sweep(a_fx[s0:], hp_a[s0:], "hp")
    if st == "filter":
        a_fx[s0:] = _sweep(a_fx[s0:], lp_a[s0:], "lp")
    a_out = np.where((tau < 0)[:, None], ax, a_fx * ga[:, None])  # identical to the old song until the transition begins
    if echo_send is not None:
        src = ax * echo_send[:, None]
        delay = int(round(0.75 * beat * SR))
        for k in range(1, 7):  # a few repeats, each softer
            sh = k * delay
            if sh < n:
                a_out[sh:] += 0.6 ** (k - 1) * src[: n - sh]
    b_fx = _sweep(bx[s0:], hp_b[s0:], "hp") if st != "fade" else bx[s0:]
    b_out = np.zeros_like(bx)
    b_out[s0:] = b_fx * gb[s0:, None]
    out = a_out + b_out
    over = np.abs(out) > 0.95  # a gentle limit, only where the sum would be too loud
    out = np.where(over, np.sign(out) * (0.95 + 0.05 * np.tanh((np.abs(out) - 0.95) / 0.05)), out)
    return out.astype(np.float32)


def _encode(pcm, dest: Path) -> None:
    tmp = dest.with_name(dest.stem + ".tmp.m4a")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)]
    subprocess.run(cmd, input=pcm.astype("<f4").tobytes(), check=True, timeout=90, capture_output=True)
    os.replace(tmp, dest)


def render(a_path: Path, b_path: Path, p: dict, dest: Path) -> dict:
    """Makes the bridge file for a plan. -> {"align_ms", "align_strength", "b_resume", "duration"}."""
    shift, strength = _measure_offset(a_path, b_path, p)
    mix, rate = p["mix_sec"], p["rate"]
    a = _decode(a_path, p["a_start"], LEAD + mix + 0.5)
    b = _decode(b_path, p["x"], (mix + TAIL + 1.0) * rate, rate)  # stretched: (mix + TAIL + 1) seconds long
    pcm = mix_pieces(a, b, p, -shift)  # B's beats fall `shift` s late: it has to start that much earlier
    dest.parent.mkdir(parents=True, exist_ok=True)
    _encode(pcm, dest)
    total = len(pcm) / SR
    started = LEAD - shift  # where on the bridge's clock the new song's piece begins
    return {"align_ms": round(-shift * 1000, 1), "align_strength": round(strength, 2), "duration": round(total, 3),
            "b_resume": round(p["x"] + rate * (total - started), 3)}


# ---------- Cache ----------

def _key(a_id: str, b_id: str, a_path: Path, b_path: Path, p: dict) -> str:
    def stat(x: Path) -> list:
        s = x.stat()
        return [s.st_size, int(s.st_mtime)]
    blob = json.dumps([RENDER_VERSION, bpmlib.ANALYSIS_VER, a_id, b_id, stat(a_path), stat(b_path),
                       {k: p[k] for k in ("style", "rate", "t_a", "mix_sec", "x", "beat_a")}], sort_keys=True)
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
