"""Tempo and beat-grid detection for DJ mode (ffmpeg + numpy, no further dependencies).

Returns the BPM and the time of the first beat (seconds) for tracks with a steady tempo, plus the
downbeat, key (Camelot), end of the intro, start of the outro and audible end. With the environment variable
BEAT_ML=1 and `beat_this` installed (optional, needs PyTorch) beats and downbeats come from the model instead;
on any error the built-in detection takes over.
For tracks without a clear beat (live, rap with tempo changes, ambient) a low confidence is returned
and the player uses a normal crossfade.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

try:
    import numpy as np
except ImportError:  # DJ mode stays off then
    np = None

SR = 11025
HOP = 128  # about 11.6 ms per frame
WIN = 512
ANALYZE_SECONDS = 150
MAX_SECONDS = 620
ANALYSIS_VER = 3  # 3: new key detection
BEAT_ML = os.getenv("BEAT_ML", "0") == "1"
MIN_BPM, MAX_BPM = 70.0, 180.0
PHASE_FIX = 0.041  # seconds, measured on synthetic click tracks


def available() -> bool:
    return np is not None


def _decode(path: Path, seconds: int = MAX_SECONDS) -> "np.ndarray":
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, timeout=90, check=True,
    )
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _onset_envelope(x: "np.ndarray", low: bool = False) -> "np.ndarray":
    """Onset strength per frame. low=True: bass only (up to about 150 Hz), shows the kick and thus the downbeat."""
    n = (len(x) - WIN) // HOP
    if n < 100:
        return np.zeros(0, dtype=np.float32)
    x = np.ascontiguousarray(x)
    win = np.hanning(WIN).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(x, shape=(n, WIN), strides=(x.strides[0] * HOP, x.strides[0]))
    spec = np.abs(np.fft.rfft(frames * win, axis=1))
    # about 20 Hz to 2.5 kHz: bass, snare, hi-hat part. Linear for the bass so the loudness difference of the kicks is preserved
    spec = spec[:, 1:8] if low else np.log1p(spec[:, 1:120] * 20)
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(43) / 43, mode="same")  # subtract the local mean
    return np.maximum(flux, 0)


def _downbeat(x: "np.ndarray", bpm: float, first_beat: float) -> tuple[float, float]:
    """Which beat is the one (4/4)? -> (time of the first downbeat, confidence)."""
    low = _onset_envelope(x[: ANALYZE_SECONDS * SR], low=True)
    if len(low) < 400:
        return first_beat, 0.0
    fpb = 60.0 / bpm * SR / HOP
    k = np.arange(0, int((len(low) - 3) / fpb) - 1)
    pos = (first_beat * SR / HOP + k * fpb).astype(int)
    pos = pos[(pos < len(low) - 2) & (pos > 6)]
    if len(pos) < 16:
        return first_beat, 0.0
    # the flux peak lies before the beat (PHASE_FIX), hence the window extends forward
    val = np.maximum.reduce([low[pos + d] for d in range(-6, 2)])
    cls = np.array([val[i::4].mean() for i in range(4)])
    best = int(cls.argmax())
    rest = np.delete(cls, best)
    conf = float((cls[best] - rest.mean()) / (cls.mean() + 1e-9))
    return first_beat + best * 60.0 / bpm, max(0.0, min(1.0, conf))


_KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]) if np is not None else None
_KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]) if np is not None else None


def _key(x: "np.ndarray") -> tuple[str, float]:
    """Key via chroma + Krumhansl profiles -> (Camelot code like "8A", confidence 0..1).
    The chroma is made from what stands out as a tone: the spectrogram is median-filtered over time (drums are short, tones last), divided by its
    running mean over frequency (a bass or a noisy hi-hat does not bury the notes) and the tuning of the recording is taken into account.
    Without that, loud bass and distorted drums made most songs come out as 8A."""
    n_fft = 8192
    if len(x) < n_fft * 8:
        return "", 0.0
    mid = len(x) // 2
    x = np.ascontiguousarray(x[max(0, mid - 60 * SR):mid + 60 * SR])  # two minutes from the middle are plenty
    hop = n_fft // 4
    n = (len(x) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(x, shape=(n, n_fft), strides=(x.strides[0] * hop, x.strides[0]))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    wide = (freqs >= 30) & (freqs <= 3000)  # a margin around the notes we use, so that the running mean is right at their edges
    spec = np.abs(np.fft.rfft(frames * np.hanning(n_fft).astype(np.float32), axis=1))[:, wide]
    k = 9
    padded = np.pad(spec, ((k // 2, k // 2), (0, 0)), mode="edge")
    harm = np.median(np.stack([padded[i:i + n] for i in range(k)]), axis=0)
    csum = np.cumsum(np.pad(harm, ((0, 0), (21, 20))).astype(np.float64), axis=1)  # float64: differences of large sums
    base = (csum[:, 41:] - csum[:, :-41]) / 41.0  # running mean over 41 bins
    tone = np.clip(harm / (base[:, :harm.shape[1]] + 1e-9) - 1.0, 0.0, None)
    use = (freqs[wide] >= 65) & (freqs[wide] <= 2100)
    tone = tone[:, use]
    midi = 12 * np.log2(freqs[wide][use] / 440.0) + 69
    frac = midi - np.round(midi)
    midi = midi - np.angle(np.sum(tone.sum(0) * np.exp(2j * np.pi * frac))) / (2 * np.pi)  # tuning of the recording
    pc = np.round(midi).astype(int) % 12
    chroma = np.array([tone[:, pc == i].sum() / max(1, int((pc == i).sum())) for i in range(12)])
    if chroma.sum() <= 0 or np.ptp(chroma) <= 0:
        return "", 0.0
    corr = []
    for t in range(12):
        c = np.roll(chroma, -t)
        corr.append((float(np.corrcoef(c, _KS_MAJ)[0, 1]), t, "B"))
        corr.append((float(np.corrcoef(c, _KS_MIN)[0, 1]), t, "A"))
    corr.sort(reverse=True)
    (r1, t, mode), r2 = corr[0], corr[1][0]
    major_pc = t if mode == "B" else (t + 3) % 12
    num = ((8 + 7 * major_pc - 1) % 12) + 1
    return f"{num}{mode}", float(max(0.0, min(1.0, (r1 - r2) / 0.12)))


def _structure(x: "np.ndarray") -> dict:
    """End of the intro, start of the outro and audible end (seconds) from the loudness curve."""
    w = SR // 2
    n = len(x) // w
    if n < 20:
        return {}
    r = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1))
    r = np.convolve(r, np.ones(3) / 3, mode="same")
    L = float(np.percentile(r, 75))
    if L <= 1e-4:
        return {}
    hi = 0.65 * L
    audible = np.where(r > 0.03 * L)[0]
    end_i = int(audible[-1]) + 1 if len(audible) else n
    intro_i = 0
    for i in range(0, max(0, end_i - 4)):
        if (r[i:i + 4] >= hi).all():
            intro_i = i
            break
    loud = np.where(r[:end_i] >= hi)[0]
    outro_i = int(loud[-1]) + 1 if len(loud) else end_i
    outro_i = max(outro_i, intro_i)
    if (end_i - outro_i) * 0.5 < 4:
        outro_i = end_i
    return {"intro_end": round(intro_i * 0.5, 1), "outro_start": round(outro_i * 0.5, 1), "audio_end": round(end_i * 0.5, 1)}


def _ml_beats(path: Path) -> dict | None:
    """Optional: beats and downbeats from the `beat_this` model (only with BEAT_ML=1)."""
    if not BEAT_ML:
        return None
    try:
        from beat_this.inference import File2Beats  # type: ignore
        beats, downbeats = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)(str(path))
        beats = np.asarray(beats, dtype=float)
        if len(beats) < 16:
            return None
        d = np.diff(beats)
        bpm = 60.0 / float(np.median(d))
        reg = 1.0 - min(1.0, float(np.std(d) / np.median(d)) * 6)
        fb = float(beats[0]) % (60.0 / bpm)
        db = float(downbeats[0]) if len(downbeats) else fb
        return {"bpm": round(bpm, 2), "first_beat": round(fb, 3), "conf": round(max(0.0, reg), 2), "first_bar": round(db, 3), "bar_ok": len(downbeats) > 3}
    except Exception:
        return None


def _comb_score(env: "np.ndarray", bpm: float, phase_steps: int = 40) -> tuple[float, float]:
    """Best phase value (seconds) and score for a tempo."""
    frames_per_beat = 60.0 / bpm * SR / HOP
    best = (-1.0, 0.0)
    n = len(env)
    for i in range(phase_steps):
        ph = frames_per_beat * i / phase_steps
        pos = np.arange(ph, n - 1, frames_per_beat)
        lo = pos.astype(int)
        fr = pos - lo
        s = float((env[lo] * (1 - fr) + env[lo + 1] * fr).sum() / max(len(pos), 1))
        if s > best[0]:
            best = (s, ph * HOP / SR)
    return best[0], best[1]


def analyze(path: Path) -> dict | None:
    """-> {"bpm", "first_beat", "conf", "first_bar", "bar_ok", "key", "key_conf", "intro_end", "outro_start", "audio_end", "ver"}
    or None if nothing was detected."""
    if np is None:
        return None
    try:
        x = _decode(path)
        env = _onset_envelope(x[: ANALYZE_SECONDS * SR])
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if len(env) < 400 or env.max() <= 0:
        return None
    ml = _ml_beats(path)
    if ml:
        res = ml
    else:
        res = _beat_grid(env)
        if not res:
            return None
        fb, conf = _downbeat(x, res["bpm"], res["first_beat"])
        res.update(first_bar=round(fb, 3), bar_ok=conf >= 0.25)
    try:
        key, kc = _key(x)
        res.update(key=key, key_conf=round(kc, 2), **_structure(x))
    except (ValueError, FloatingPointError):
        pass
    res["ver"] = ANALYSIS_VER
    return res


def _beat_grid(env: "np.ndarray") -> dict | None:
    env = env / (env.max() + 1e-9)
    # coarse: autocorrelation over the tempo range
    lo_lag = int(60.0 / MAX_BPM * SR / HOP)
    hi_lag = int(60.0 / MIN_BPM * SR / HOP)
    ac = np.correlate(env - env.mean(), env - env.mean(), mode="full")[len(env) - 1:]
    ac = ac / (ac[0] + 1e-9)
    lags = np.arange(lo_lag, hi_lag + 1)
    cand = lags[np.argsort(ac[lo_lag:hi_lag + 1])[::-1][:6]]
    coarse = sorted({round(60.0 * SR / HOP / l, 1) for l in cand})
    # fine: every candidate tempo +-2 BPM in steps of 0.05 via comb score
    best = (-1.0, 0.0, 0.0)
    for c in coarse:
        for bpm in np.arange(c - 2.0, c + 2.0001, 0.05):
            if not MIN_BPM <= bpm <= MAX_BPM:
                continue
            s, ph = _comb_score(env, float(bpm), 24)
            if s > best[0]:
                best = (s, float(bpm), ph)
    if best[1] <= 0:
        return None
    # refine the phase at full resolution
    s, ph = _comb_score(env, best[1], 96)
    conf = max(0.0, min(1.0, (s / (float(env.mean()) + 1e-9) - 1.0) / 6.0))  # noise about 0.1, clear beat 1.0
    bpm = best[1]
    ph = (ph + PHASE_FIX) % (60.0 / bpm)  # window offset: the flux peak lies before the actual beat
    return {"bpm": round(bpm, 2), "first_beat": round(ph, 3), "conf": round(conf, 2)}


def _thin(a: "np.ndarray", points: int) -> list[float]:
    """At most `points` values (the largest of each stretch), scaled to 0..1, for drawing."""
    if len(a) == 0:
        return []
    if len(a) > points:
        edges = np.linspace(0, len(a), points + 1).astype(int)
        a = np.array([a[i:j].max() if j > i else 0.0 for i, j in zip(edges[:-1], edges[1:])])
    top = float(a.max()) or 1.0
    return [round(float(v) / top, 3) for v in a]


def curves(path: Path, points: int = 600) -> dict | None:
    """For the developer tools: the curves the analysis looks at, thinned out so that they can be drawn.
    -> {"seconds", "loudness" (per `loudness_step` seconds), "onset" (per `onset_step` seconds, only the part that is analyzed)} or None."""
    if np is None:
        return None
    try:
        x = _decode(path)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    w = SR // 2
    n = len(x) // w
    loud = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1)) if n else np.zeros(0)
    env = _onset_envelope(x[: ANALYZE_SECONDS * SR])
    loud_pts, env_pts = _thin(loud, points), _thin(env, points)
    return {"seconds": round(len(x) / SR, 1), "loudness": loud_pts, "loudness_step": round(0.5 * len(loud) / max(1, len(loud_pts)), 3),
            "onset": env_pts, "onset_step": round(len(env) * HOP / SR / max(1, len(env_pts)), 3)}
