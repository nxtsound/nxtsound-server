"""Per-song cache for the vocal separation DJ_STEMS does (see stems.py, djmix.py): the few seconds near a song's own start and end, where a DJ
transition can ever cut it, separated once per song and kept - instead of once per song PAIR, which is what happens without this cache (still
works the same, just slower the first time a given pair is asked for).

A background worker (`start`, hooked into `library.AFTER_SCAN` by main.py) works through the library while the server is otherwise idle. Any
live bridge render (a listener is waiting) takes priority: the worker checks before every `CHUNK` seconds of the model's work whether one is
under way and, if so, lets go and waits rather than start more. It cannot interrupt a chunk already running (the model does not support
that), so the most a live request ever waits on the worker is one chunk - the same single `djmix._render_lock` a live render would wait on
anyway without this cache, just no longer than before this cache existed.

Correctness never depends on this cache: `read()` only hands back audio for a stretch it is sure it covers, and callers fall back to running
the model themselves for anything else (a new song, one the background worker has not reached yet, or a request that lands just outside the
cached window) - exactly as if this file did not exist.
"""
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

import db
import library
import stems

try:
    import numpy as np
except ImportError:  # available() is False then anyway (stems.py needs it too): _precompute is never reached
    np = None

log = logging.getLogger("stemcache")

CACHE_VER = 1  # raise when the cached window changes shape: old entries are then made again
WINDOW = 40.0  # seconds from the very start, and from the very end, of a song: generous enough for any transition this server can make
CHUNK = 10.0   # seconds of audio per call to the model: the most a live render ever has to wait for the worker to let go of it
YIELD_AFTER_CHUNK = 0.5  # a pause after every chunk, so a live render that has been waiting actually gets to go first, not the worker again
LIVE_GRACE = 10.0   # seconds after a live render that the worker stays out of the way, in case the next one follows close behind
POLL_INTERVAL = 3.0  # how often the worker checks again while it is waiting for a live render to be over
IDLE_INTERVAL = 30.0  # how often it checks for new songs once the library is fully cached
CACHE_DIR = Path(os.getenv("DJ_STEMS_CACHE_DIR") or Path(db.DB_PATH).parent / "dj-stem-cache")
MAX_BYTES = int(float(os.getenv("DJ_STEMS_CACHE_MB", "0")) * 1024 * 1024)  # 0 (default) = unlimited

_lock = threading.Lock()
_status = {"state": "idle", "total": 0, "done": 0, "bytes": 0, "current": None}
_live_until = 0.0
_thread: threading.Thread | None = None


def available() -> bool:
    return stems.available()


def mark_live() -> None:
    """Call this whenever a listener's own bridge is about to use the model: the background worker leaves the processor alone for a while."""
    global _live_until
    _live_until = time.monotonic() + LIVE_GRACE


def _live_recently() -> bool:
    return time.monotonic() < _live_until


def status() -> dict:
    """For the admin area: how far the background separation has got. `state`: idle (DJ_STEMS is off), running, paused (a listener's own
    transition is being made right now) or done (nothing left to separate, for now)."""
    with _lock:
        return dict(_status)


def _paths(track_id: str) -> tuple[Path, Path]:
    h = hashlib.sha1(track_id.encode()).hexdigest()
    return CACHE_DIR / f"{h}.a.m4a", CACHE_DIR / f"{h}.b.m4a"


def read(track_id: str, path: Path, side: str, source_start: float, source_length: float, rate: float) -> "np.ndarray | None":  # noqa: F821
    """The separated singing for `source_length` seconds of `path`'s own timeline starting at `source_start`, stretched by `rate` (1.0 = not
    stretched), if a cache for `track_id` covers it - else None (the caller runs the model itself). `side`: "a" (the song as it fades out of a
    transition) or "b" (as it fades in)."""
    if source_length <= 0:
        return None
    row = db.stem_cache_get(track_id)
    if not row or row["ver"] != CACHE_VER:
        return None
    start, length = (row["a_start"], row["a_len"]) if side == "a" else (0.0, row["b_len"])
    if source_start < start - 1e-3 or source_start + source_length > start + length + 1e-3:
        return None  # outside what was cached (an unusually long transition): the live model covers it instead
    cache_path = _paths(track_id)[0 if side == "a" else 1]
    if not cache_path.is_file():
        return None
    import djmix  # noqa: PLC0415 (djmix imports this module too; see the module docstring)
    try:
        pcm = djmix._decode(cache_path, source_start - start, source_length, rate)
    except Exception as e:
        log.warning("stem cache: could not read %s (%s): %s", track_id, side, e)
        return None
    db.stem_cache_touch(track_id)
    return pcm


# ---------- The background worker ----------

def _separate_window(path: Path, start: float, length: float) -> "np.ndarray | None":
    """The song's own singing for `length` seconds of `path` from `start`, done `CHUNK` seconds at a time so a live render is never kept
    waiting for more than one chunk - None if a live render came in partway (the rest of this window is picked up again later)."""
    import djmix  # noqa: PLC0415
    pieces = []
    done = 0.0
    while done < length:
        if _live_recently():
            return None
        step = min(CHUNK, length - done)
        with djmix._render_lock:
            if _live_recently():
                return None
            pcm = djmix._decode(path, start + done, step)
            if len(pcm) == 0:
                break
            pieces.append(stems.vocals(pcm))
        done += step
        time.sleep(YIELD_AFTER_CHUNK)  # let a render that started waiting while the lock was held actually get it next, not this loop again
    return np.concatenate(pieces, axis=0) if pieces else np.zeros((0, 2), dtype=np.float32)


def _precompute(track_id: str, path: Path, dur: float) -> None:
    import djmix  # noqa: PLC0415
    a_start = max(0.0, dur - WINDOW)
    a_len = dur - a_start
    b_len = min(WINDOW, dur)
    out_a, out_b = _paths(track_id)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for side, dest, start, length in (("a", out_a, a_start, a_len), ("b", out_b, 0.0, b_len)):
        vocals = _separate_window(path, start, length)
        if vocals is None or len(vocals) == 0:
            return  # interrupted by a live request, or nothing decoded: the rest (and this side again) is picked up next time
        djmix._encode(vocals, dest)
    size = (out_a.stat().st_size if out_a.is_file() else 0) + (out_b.stat().st_size if out_b.is_file() else 0)
    db.stem_cache_set(track_id, CACHE_VER, a_start, a_len, b_len, size)
    for dropped in db.stem_cache_evict(MAX_BYTES):
        for f in _paths(dropped):
            f.unlink(missing_ok=True)


def _set_status(**kw) -> None:
    with _lock:
        _status.update(kw)


def _run() -> None:
    import djmix  # noqa: PLC0415
    while True:
        if not available():
            _set_status(state="idle", total=0, done=0, current=None)
            time.sleep(IDLE_INTERVAL)
            continue
        todo = db.stem_cache_todo(djmix.MIN_CONF, CACHE_VER)
        stats = db.stem_cache_stats(djmix.MIN_CONF)
        _set_status(total=stats["total"], done=stats["done"], bytes=stats["bytes"])
        if not todo:
            _set_status(state="done", current=None)
            time.sleep(IDLE_INTERVAL)
            continue
        for row in todo:
            if _live_recently():
                _set_status(state="paused")
                time.sleep(POLL_INTERVAL)
                break  # re-read `todo`: the library or the cache may have changed while waiting
            path = library.file_path(row["id"])
            if not path:
                db.stem_cache_set(row["id"], CACHE_VER, 0, 0, 0, 0)  # gone from disk: do not keep asking
                continue
            _set_status(state="running", current=row["id"])
            try:
                _precompute(row["id"], path, float(row["duration"]))
            except Exception:
                log.exception("stem cache: %s", row["id"])
            time.sleep(0.2)  # do not keep the server busy permanently, like the tempo analysis does


def start() -> None:
    """Starts the background worker if it is not already running. Safe to call repeatedly (e.g. after every library scan)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, daemon=True, name="stem-cache")
    _thread.start()
