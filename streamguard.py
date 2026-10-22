"""Limits how many streams of one account play at the same time (Admin area -> System). Every player (a browser tab, an app) has an ID of its own; one that
starts playing claims a place under its ID and keeps it with a heartbeat. One that pauses, stops or disappears frees it (no heartbeat for TTL seconds).
A player that already holds a place never loses it to a newcomer: the newcomer is told no. So ten tabs in one login count as ten, not as one.
Only in memory: after a restart the players claim again with their next heartbeat."""
import os
import threading
import time

import db

SETTING = "max_streams"
DEFAULT_LIMIT = int(os.getenv("MAX_STREAMS_PER_USER", "0"))  # 0 = no limit
MAX_LIMIT = 50
TTL = 45  # seconds without a heartbeat after which a device no longer counts (the web app beats every 15 s)

_slots: dict[int, dict[tuple[int, str], float]] = {}  # user ID -> {(session ID, player ID): time of the last heartbeat}
_lock = threading.Lock()


def limit() -> int:
    """What the admin chose, else the environment's value (a broken stored value falls back to it)."""
    raw = db.get_setting(SETTING, "")
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_LIMIT
    return n if 0 <= n <= MAX_LIMIT else DEFAULT_LIMIT


def set_limit(n: int) -> None:
    db.set_setting(SETTING, str(n))


def _alive(user_id: int, now: float) -> dict[tuple[int, str], float]:
    live = {key: t for key, t in _slots.get(user_id, {}).items() if now - t < TTL}
    if live:
        _slots[user_id] = live
    else:
        _slots.pop(user_id, None)
    return live


def claim(user_id: int, session_id: int, player: str = "") -> tuple[bool, int, int]:
    """(allowed, streams playing now including this one if allowed, limit). Idempotent: the heartbeat is the same call.
    A player that sends no ID (an older page) is told apart only by its login."""
    now, cap, key = time.time(), limit(), (session_id, player)
    with _lock:
        live = _alive(user_id, now)
        if key not in live and cap and len(live) >= cap:
            return False, len(live), cap
        live[key] = now
        _slots[user_id] = live
        return True, len(live), cap


def release(user_id: int, session_id: int, player: str = "") -> None:
    with _lock:
        live = _alive(user_id, time.time())
        live.pop((session_id, player), None)
        if not live:
            _slots.pop(user_id, None)


def playing() -> dict[int, int]:
    """User ID -> number of streams playing now (for the admin)."""
    now = time.time()
    with _lock:
        return {uid: len(live) for uid in list(_slots) if (live := _alive(uid, now))}


def reset() -> None:
    """Forgets who plays (tests)."""
    with _lock:
        _slots.clear()
