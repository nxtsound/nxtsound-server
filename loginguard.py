"""Protection against guessing passwords: failed logins are counted per IP and name and per IP; too many of them block that IP (and name) for a while.
The admin sets the numbers (Admin area -> System); blocks are stored in the database, so a restart does not lift them. Counting itself is in memory."""
import os
import threading
import time

import db

SETTING_ATTEMPTS = "login_max_attempts"
SETTING_BLOCK = "login_block_minutes"
DEFAULT_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))      # failed logins before the block; 0 = never block
DEFAULT_BLOCK_MINUTES = int(os.getenv("LOGIN_BLOCK_MINUTES", "5"))
MAX_ATTEMPTS, MAX_BLOCK_MINUTES = 100, 1440
IP_FACTOR = 4         # one IP may fail this many times more across all names than for a single name
COUNT_MINUTES = 15    # failures older than this (or than the block, if that is longer) are forgotten

_fails: dict[str, list[float]] = {}  # key -> times of failed attempts
_lock = threading.Lock()


def _valid(value: str, low: int, high: int) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def settings() -> tuple[int, int]:
    """(attempts, block minutes): what the admin chose, else the environment's value (invalid values fall back to the default)."""
    attempts = _valid(db.get_setting(SETTING_ATTEMPTS, ""), 0, MAX_ATTEMPTS)
    minutes = _valid(db.get_setting(SETTING_BLOCK, ""), 1, MAX_BLOCK_MINUTES)
    return (DEFAULT_ATTEMPTS if attempts is None else attempts), (DEFAULT_BLOCK_MINUTES if minutes is None else minutes)


def set_settings(attempts: int, minutes: int) -> None:
    db.set_setting(SETTING_ATTEMPTS, str(attempts))
    db.set_setting(SETTING_BLOCK, str(minutes))


def _keys(ip: str, name: str) -> tuple[str, str]:
    return f"{ip}|{name.lower()}", f"{ip}|*"


def blocked_seconds(ip: str, name: str) -> int:
    """How long this attempt is still blocked (0 = not blocked)."""
    if settings()[0] == 0:
        return 0
    until = db.login_block_until(list(_keys(ip, name)))
    return max(0, int(until - time.time()) + 1) if until else 0


def record_fail(ip: str, name: str) -> None:
    attempts, minutes = settings()
    if attempts == 0:
        return
    now, window = time.time(), max(minutes * 60, COUNT_MINUTES * 60)
    with _lock:
        if len(_fails) > 5000:  # bound memory use
            for k in [k for k, v in _fails.items() if not v or now - v[-1] > window]:
                _fails.pop(k, None)
        for key, limit in zip(_keys(ip, name), (attempts, attempts * IP_FACTOR)):
            fails = [t for t in _fails.get(key, []) if now - t < window] + [now]
            if len(fails) >= limit:
                db.add_login_block(key, now + minutes * 60)
                _fails.pop(key, None)  # counting starts over after the block
            else:
                _fails[key] = fails


def record_ok(ip: str, name: str) -> None:
    """A correct login forgives the earlier typos for this name (the IP-wide count stays)."""
    with _lock:
        _fails.pop(_keys(ip, name)[0], None)


def blocks() -> list[dict]:
    """The running blocks for the admin: IP, name ("" = the whole IP) and when they end."""
    out = []
    for b in db.login_blocks():
        ip, _, name = b["key"].rpartition("|")
        out.append({"ip": ip, "name": "" if name == "*" else name, "until": b["until"]})
    return out


def unblock(ip: str | None = None, name: str = "") -> int:
    """Lifts one block (name "" = the IP-wide one) or, without an ip, every block."""
    with _lock:
        if ip is None:
            _fails.clear()
            return db.delete_login_blocks()
        key = f"{ip}|{name.lower() or '*'}"
        _fails.pop(key, None)
        return db.delete_login_blocks(key)


def reset() -> None:
    """Forgets the counted failures (tests)."""
    with _lock:
        _fails.clear()
