"""Looks after the server itself: low disk space, a newer server version on GitHub, newer versions of plugins installed from a repository.

What it finds goes to the admins' bell (notifications, target SYSTEM). A message appears once and disappears again when the reason is gone
(disk space is fine again, the update was installed). Nothing here changes the server or a plugin.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time

import httpx

import db
import library
import pluginhost

log = logging.getLogger("server")

DEFAULT_REPO = "nxtsound/nxtsound-server"  # the project's public repository: its newest release (or, before there is one, its newest tag) is the newest version
UPDATE_REPO = os.getenv("UPDATE_REPO", DEFAULT_REPO).strip()  # "owner/repo" of a fork; empty = do not look
CHANNELS = ("stable", "beta")  # stable: the newest release; beta: also pre-releases (v0.2.0-beta.1)
UPDATE_CHANNEL = os.getenv("UPDATE_CHANNEL", "stable").strip().lower()  # the default; the admin's choice (stored in the database) wins
UPDATE_CHECK_HOURS = float(os.getenv("UPDATE_CHECK_HOURS", "6"))  # how often to look for new versions of the server and the plugins; 0 = never
DISK_WARN_GB = float(os.getenv("DISK_WARN_GB", "1"))  # warn below this much free space ...
DISK_WARN_PERCENT = float(os.getenv("DISK_WARN_PERCENT", "10"))  # ... or below this share
DISK_EVERY = 1800  # seconds between two looks at the disks
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
CHANNEL_SETTING = "update_channel"
SETTING = "server_update"  # the last answer about the server version, kept so the admin area has it right after a restart
NOTES_MAX = 4000

_transport = None  # tests: an httpx transport instead of the network
_state = {"server": None, "plugins_checked": 0.0, "server_checked": 0.0, "server_error": ""}


def set_transport(transport) -> None:
    global _transport
    _transport = transport


def version_tuple(v: str) -> tuple[int, ...]:
    """'v0.2.10-rc1' -> (0, 2, 10). Only the numbers of the first part count (see version_key for the full order)."""
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0].lstrip("vV"))) or (0,)


_VERSION = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


def version_key(v: str) -> tuple:
    """A sortable key following semantic versioning: 0.2.0-beta.1 < 0.2.0-beta.2 < 0.2.0 < 0.2.1. Text that is no version sorts lowest."""
    m = _VERSION.match((v or "").strip())
    if not m:
        return ((0, 0, 0, 0), (0, ()))
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (4 - len(nums)) if len(nums) < 4 else nums
    if not m.group(2):
        return (nums, (1, ()))  # a release comes after all its pre-releases
    return (nums, (0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m.group(2).split("."))))


def is_prerelease(v: str) -> bool:
    m = _VERSION.match((v or "").strip())
    return bool(m and m.group(2))


def current_version() -> str:
    import main
    return main.VERSION_INFO["version"]


def channel() -> str:
    """The update channel: the admin's choice, else UPDATE_CHANNEL, else stable."""
    for c in (db.get_setting(CHANNEL_SETTING, ""), UPDATE_CHANNEL):
        if c in CHANNELS:
            return c
    return "stable"


def set_channel(value: str) -> None:
    if value not in CHANNELS:
        raise ValueError(value)
    db.set_setting(CHANNEL_SETTING, value)
    _state["server"] = None  # the last answer belongs to the other channel
    db.set_setting(SETTING, "")


# ---------- Server version on GitHub ----------

def _release(d: dict) -> dict:
    tag = str(d.get("tag_name") or "")
    return {"version": tag.lstrip("vV"), "tag": tag, "notes": str(d.get("body") or "")[:NOTES_MAX], "url": str(d.get("html_url") or ""), "prerelease": bool(d.get("prerelease")) or is_prerelease(tag)}


def latest_release(repo: str, channel: str = "stable") -> dict | None:
    """The newest version on GitHub for a channel: {"version", "tag", "notes", "url", "prerelease"}, or None if there is nothing.

    stable: the newest release that is not a pre-release (GitHub's "latest"); without any release, the newest tag without a pre-release suffix.
    beta: the newest of all releases including pre-releases; without any release, the newest tag.
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "nxtsound-server", "X-GitHub-Api-Version": "2022-11-28"}
    beta = channel == "beta"
    with httpx.Client(timeout=10, headers=headers, follow_redirects=True, **({"transport": _transport} if _transport else {})) as c:
        if beta:
            r = c.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30})
            if r.status_code == 200:
                found = [_release(x) for x in r.json() if not x.get("draft") and re.search(r"\d", str(x.get("tag_name") or ""))]
                if found:
                    return max(found, key=lambda x: version_key(x["version"]))
            elif r.status_code != 404:
                r.raise_for_status()
        else:
            r = c.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 200:
                d = r.json()
                return {**_release(d), "url": str(d.get("html_url") or f"https://github.com/{repo}/releases")}
            if r.status_code != 404:
                r.raise_for_status()
        t = c.get(f"https://api.github.com/repos/{repo}/tags", params={"per_page": 50})  # no release (yet): look at the tags
        if t.status_code == 200 and t.json():
            names = [x["name"] for x in t.json() if re.search(r"\d", x.get("name", "")) and (beta or not is_prerelease(x["name"]))]
            if names:
                best = max(names, key=version_key)
                return {"version": best.lstrip("vV"), "tag": best, "notes": "", "url": f"https://github.com/{repo}/releases/tag/{best}", "prerelease": is_prerelease(best)}
    return None


def check_server_update() -> dict:
    """Asks GitHub whether a newer server version exists (in the chosen channel); stores and returns {"enabled", "current", "latest", "available", ...}."""
    cur, ch = current_version(), channel()
    out = {"enabled": bool(UPDATE_REPO and REPO_RE.match(UPDATE_REPO)), "repo": UPDATE_REPO, "channel": ch, "current": cur, "available": False, "latest": "", "notes": "", "url": "", "prerelease": False, "checked": time.time()}
    if not UPDATE_REPO or not REPO_RE.match(UPDATE_REPO):
        _state["server"] = out
        return out
    try:
        rel = latest_release(UPDATE_REPO, ch)
        _state["server_error"] = ""
    except (httpx.HTTPError, ValueError) as e:
        _state["server_error"] = e.__class__.__name__
        log.info("Looking for a new server version failed: %s", e.__class__.__name__)
        return snapshot()["server"]
    if rel:
        out.update(latest=rel["version"], notes=rel["notes"], url=rel["url"], prerelease=rel["prerelease"], available=version_key(rel["version"]) > version_key(cur))
    _state["server"] = out
    _state["server_checked"] = out["checked"]
    db.set_setting(SETTING, json.dumps(out))
    if out["available"]:
        db.notify(db.SYSTEM, "server_update", "server_update", {"current": cur, "latest": out["latest"], "url": out["url"], "prerelease": out["prerelease"]})
    else:
        db.resolve_notification(db.SYSTEM, "server_update")
    return out


# ---------- Disk space ----------

def volumes() -> list[tuple[str, str]]:
    """(name, path) of what can fill up: the data folder and the music folder (once, if they are on the same disk)."""
    out, seen = [], set()
    for name, path in (("data", db.DB_PATH.parent), ("music", library.MUSIC_DIR)):
        try:
            dev = os.stat(path).st_dev
        except OSError:
            continue
        if dev not in seen:
            seen.add(dev)
            out.append((name, str(path)))
    return out


def check_disks() -> list[dict]:
    """Warns about disks with little free space; the warning goes away when there is room again (with some margin, so it does not flicker)."""
    problems = []
    for name, path in volumes():
        try:
            u = shutil.disk_usage(path)
        except OSError:
            continue
        free_pct = u.free * 100 / u.total if u.total else 100
        key = f"disk_low:{name}"
        low = u.free < DISK_WARN_GB * 1024**3 or free_pct < DISK_WARN_PERCENT
        fine = u.free >= DISK_WARN_GB * 1.5 * 1024**3 and free_pct >= DISK_WARN_PERCENT + 2
        if low:
            problems.append({"name": name, "path": path, "free": u.free, "total": u.total})
            db.notify(db.SYSTEM, key, "disk_low", {"name": name, "path": path, "free_gb": round(u.free / 1024**3, 1), "total_gb": round(u.total / 1024**3, 1), "percent": round(free_pct)})
        elif fine:
            db.resolve_notification(db.SYSTEM, key)
    return problems


# ---------- Plugins ----------

def check_plugins() -> dict[str, dict]:
    found = pluginhost.check_all_updates()
    _state["plugins_checked"] = time.time()
    wanted = set()
    for pid, u in found.items():
        if u.get("available"):
            key = f"plugin_update:{pid}"
            wanted.add(key)
            name = next((p["name"] for p in pluginhost.plugins.list_plugins() if p["id"] == pid), pid)
            db.notify(db.SYSTEM, key, "plugin_update", {"id": pid, "name": name, "version": u.get("version", ""), "latest": u.get("latest", "")})
    db.resolve_notifications_like(db.SYSTEM, "plugin_update:", wanted)
    return found


# ---------- Together ----------

def run_checks(updates: bool = True) -> dict:
    """One round: disks always, new versions (server and plugins) if `updates`. Each part is on its own: one failing does not stop the others."""
    for part in ((check_disks,) + ((check_server_update, check_plugins) if updates else ())):
        try:
            part()
        except Exception:  # noqa: BLE001 (a failing check must never take the server down)
            log.exception("Check %s failed", part.__name__)
    return snapshot()


def snapshot() -> dict:
    """What the admin area shows about versions."""
    server = _state["server"]
    if server is None:  # after a restart: the last answer
        try:
            server = json.loads(db.get_setting(SETTING, "")) or None
        except ValueError:
            server = None
    server = dict(server or {"enabled": bool(UPDATE_REPO), "repo": UPDATE_REPO, "available": False, "latest": "", "notes": "", "url": "", "prerelease": False, "checked": 0})
    server.update(current=current_version(), channel=channel(), enabled=bool(UPDATE_REPO and REPO_RE.match(UPDATE_REPO)), repo=UPDATE_REPO, error=_state["server_error"])
    if server["latest"]:
        server["available"] = version_key(server["latest"]) > version_key(server["current"])
    return {"server": server, "plugins": dict(pluginhost.UPDATES), "check_hours": UPDATE_CHECK_HOURS, "channels": list(CHANNELS)}


async def loop() -> None:
    """Looks at the disks every half hour and for new versions every UPDATE_CHECK_HOURS hours (the first look comes a minute after the start)."""
    await asyncio.sleep(60)
    last_updates = 0.0
    while True:
        due = UPDATE_CHECK_HOURS > 0 and time.time() - last_updates >= UPDATE_CHECK_HOURS * 3600
        try:
            await asyncio.to_thread(run_checks, due)
        except Exception:  # noqa: BLE001
            log.exception("System check failed")
        if due:
            last_updates = time.time()
        await asyncio.sleep(DISK_EVERY)
