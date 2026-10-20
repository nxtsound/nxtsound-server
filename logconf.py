"""Logging configuration: what the server logs, adjustable by environment variables and in the admin area.

Log messages are grouped into categories (server, access log, library scan, ...). The level of every category is resolved as:

1. admin setting (stored in the database, changes apply immediately, no restart),
2. environment variable `LOG_LEVEL_<CATEGORY>` (e.g. `LOG_LEVEL_LIBRARY=DEBUG`),
3. environment variable `LOG_LEVEL` (default for the server's own categories; `access` and `other` have their own defaults),
4. the built-in default.

Levels: OFF, ERROR, WARNING, INFO, DEBUG. A small ring buffer keeps the latest messages so the admin area can show them
without access to the container's output.
"""
import collections
import json
import logging
import os
import re
import threading
import time

import db
from i18n import tr

LEVELS = ("OFF", "ERROR", "WARNING", "INFO", "DEBUG")
_NUM = {"OFF": logging.CRITICAL + 10, "ERROR": logging.ERROR, "WARNING": logging.WARNING, "INFO": logging.INFO, "DEBUG": logging.DEBUG}
SETTING = "log_levels"
BUFFER_SIZE = 500

# id, label (translated for the admin area), logger names, built-in default, follows LOG_LEVEL
CATEGORIES = [
    ("server", "Server", ["server"], "INFO", True),
    ("access", "Access log (HTTP requests)", ["uvicorn.access"], "INFO", False),
    ("library", "Library scan", ["library"], "INFO", True),
    ("enrich", "Information from the internet", ["enrich"], "INFO", True),
    ("plugins", "Plugin management", ["plugins", "pluginhost"], "INFO", True),
    ("plugin_output", "Plugin output", ["plugin"], "WARNING", True),
    ("other", "Other libraries", [""], "WARNING", False),
]
IDS = [c[0] for c in CATEGORIES]
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_buffer: collections.deque = collections.deque(maxlen=BUFFER_SIZE)
_plugin_buffers: dict[str, collections.deque] = {}  # plugin id -> its own latest messages (so a chatty plugin cannot push the others out of view)
PLUGIN_BUFFER_SIZE = 300
_PLUGIN_RE = re.compile(r"\b[Pp]lugin (\w[\w-]*)")  # "Plugin yt running ...", "Settings of plugin yt changed"
_lock = threading.Lock()
_installed = False


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {"time": record.created, "level": record.levelname, "logger": record.name, "message": record.getMessage()}
            _buffer.append(entry)
            if record.name in ("plugins", "pluginhost") and (m := _PLUGIN_RE.search(entry["message"])):  # the server talking about a plugin
                _plugin_buffers.setdefault(m.group(1), collections.deque(maxlen=PLUGIN_BUFFER_SIZE)).append(entry)
            # (a plugin's own output arrives through plugin_line(), whatever the log level is)
        except Exception:  # noqa: BLE001 (logging must never raise)
            pass


def _parse(value: str) -> str:
    v = (value or "").strip().upper()
    return v if v in LEVELS else ""


def _env_level(cat_id: str, follows_global: bool) -> str:
    specific = _parse(os.getenv(f"LOG_LEVEL_{cat_id.upper()}", ""))
    if specific:
        return specific
    if cat_id == "access" and os.getenv("LOG_ACCESS", "") in ("0", "false", "no"):
        return "OFF"
    return _parse(os.getenv("LOG_LEVEL", "")) if follows_global else ""


def admin_levels() -> dict[str, str]:
    """Levels saved in the admin area (category id -> level)."""
    try:
        raw = json.loads(db.get_setting(SETTING, "{}"))
    except ValueError:
        return {}
    return {k: v for k, v in raw.items() if k in IDS and v in LEVELS} if isinstance(raw, dict) else {}


def resolve() -> dict[str, tuple[str, str]]:
    """category id -> (level, source) where source is 'admin', 'env' or 'default'."""
    saved = admin_levels()
    out = {}
    for cat_id, _label, _loggers, default, follows in CATEGORIES:
        if cat_id in saved:
            out[cat_id] = (saved[cat_id], "admin")
        elif (env := _env_level(cat_id, follows)):
            out[cat_id] = (env, "env")
        else:
            out[cat_id] = (default, "default")
    return out


def _install_handlers() -> None:
    """Stderr output for the application's own loggers (uvicorn configures its own) and the ring buffer for the admin area."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
        root = logging.getLogger()
        if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _RingHandler) for h in root.handlers):
            stream = logging.StreamHandler()
            stream.setFormatter(logging.Formatter(FORMAT))
            root.addHandler(stream)
        ring = _RingHandler()
        for name in ("", "uvicorn.error", "uvicorn.access"):  # uvicorn's loggers do not propagate to the root logger
            logging.getLogger(name).addHandler(ring)


def apply() -> dict[str, tuple[str, str]]:
    """Sets the logger levels from the current configuration (call at startup and after every change)."""
    _install_handlers()
    resolved = resolve()
    for cat_id, _label, loggers, _default, _follows in CATEGORIES:
        for name in loggers:
            logging.getLogger(name).setLevel(_NUM[resolved[cat_id][0]])
    return resolved


def status() -> dict:
    resolved = resolve()
    return {"levels": list(LEVELS), "categories": [
        {"id": cat_id, "label": tr(label), "level": resolved[cat_id][0], "source": resolved[cat_id][1]}
        for cat_id, label, _loggers, _default, _follows in CATEGORIES]}


def set_levels(changes: dict[str, str]) -> dict:
    """Saves level changes (category id -> level, '' = back to environment/default) and applies them at once."""
    saved = admin_levels()
    for cat_id, level in changes.items():
        if cat_id not in IDS:
            raise ValueError(f"unknown category {cat_id}")
        if level == "":
            saved.pop(cat_id, None)
        elif level.upper() in LEVELS:
            saved[cat_id] = level.upper()
        else:
            raise ValueError(f"unknown level {level}")
    db.set_setting(SETTING, json.dumps(saved))
    apply()
    logging.getLogger("server").info("Log levels changed: %s", ", ".join(f"{k}={v}" for k, v in sorted(changes.items())) or "none")
    return status()


def plugin_line(plugin_id: str, line: str) -> None:
    """A line a plugin printed. It is kept for that plugin's log view even if the log level hides plugin output in the general log."""
    _plugin_buffers.setdefault(plugin_id, collections.deque(maxlen=PLUGIN_BUFFER_SIZE)).append(
        {"time": time.time(), "level": "OUTPUT", "logger": f"plugin.{plugin_id}", "message": line})


def recent_for_plugin(plugin_id: str, limit: int = 200) -> list[dict]:
    """The latest messages about one plugin (its output and what the server says about it), oldest first."""
    items = list(_plugin_buffers.get(plugin_id, ()))[-max(1, min(limit, PLUGIN_BUFFER_SIZE)):]
    return [{**i, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(i["time"]))} for i in items]


def forget_plugin(plugin_id: str) -> None:
    _plugin_buffers.pop(plugin_id, None)


def recent(limit: int = 200) -> list[dict]:
    """The latest buffered messages, oldest first, with a formatted time."""
    items = list(_buffer)[-max(1, min(limit, BUFFER_SIZE)):]
    return [{**i, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(i["time"]))} for i in items]
