"""Developer tools: extra information about why the server did what it did (scores of a radio, and so on).

Off unless the environment variable DEVTOOLS=1 is set, and then only for admins. The check lives here and is made on the server for
every request that can hand out such information; the page hiding a panel is not a protection.
"""
import collections
import os
import random
import threading
import time

ENABLED = os.getenv("DEVTOOLS", "0") == "1"

MAX_CALLS = 100
_calls: collections.deque = collections.deque(maxlen=MAX_CALLS)
_lock = threading.Lock()


def active(user: dict | None) -> bool:
    """May this user get developer information?"""
    return ENABLED and bool(user and user.get("is_admin"))


def rng(user: dict | None, seed: int | None) -> random.Random | None:
    """A dice with a fixed start for a developer who asks for one (`?rng=42`), so that a radio or a mix can be repeated; else None (real dice)."""
    return random.Random(seed) if seed is not None and active(user) else None


# ---------- Calls to plugins ----------
# Only kept while the tools are on. The body of a call is never kept (settings can hold passwords), and the answer to a settings call is left out.

def record_call(plugin_id: str, method: str, path: str, params: dict | None, status: int | str, ms: float, size: int = 0, error: str = "",
                preview: str = "") -> None:
    if not ENABLED:
        return
    entry = {"time": time.time(), "plugin": plugin_id, "method": method, "path": path, "params": params or {}, "status": status,
             "ms": round(ms, 1), "size": size, "error": error[:200], "preview": "" if path.startswith("/settings") else preview[:600]}
    with _lock:
        _calls.appendleft(entry)


def calls() -> list[dict]:
    with _lock:
        return list(_calls)


def reset() -> None:
    with _lock:
        _calls.clear()
