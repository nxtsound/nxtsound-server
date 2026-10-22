"""Backups to other places: the destinations an admin set up (a folder, S3, WebDAV, FTP, SFTP), running a backup now or on a schedule, keeping only the last few and
fetching one back for a restore. A backup is the consistent copy of the database (users, playlists, history, settings); the music files are not part of it.

The destinations are stored as JSON in the settings table. Their passwords and keys stay on the server: what goes to the admin page lists only *which* secrets are set."""
import asyncio
import datetime
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

import backupdrivers
import db
from i18n import tr

log = logging.getLogger("backup")

SETTING = "backup_targets"
SCHEDULES = ("off", "6h", "12h", "daily", "weekly")
MAX_TARGETS = 10
RETRY_AFTER = 1800  # seconds between two attempts after a failure
NOTE_KIND = "backup_failed"

_lock = threading.RLock()
_running: set[str] = set()


class BackupProblem(ValueError):
    """Something about the settings of a destination is wrong; the text is ready to be shown."""


# ---------- Destinations ----------

def load() -> list[dict]:
    with _lock:
        try:
            data = json.loads(db.get_setting(SETTING, "[]"))
        except ValueError:
            return []
        return [t for t in data if isinstance(t, dict) and t.get("type") in backupdrivers.types()]


def _save(targets: list[dict]) -> None:
    with _lock:
        db.set_setting(SETTING, json.dumps(targets, separators=(",", ":")))


def get(target_id: str) -> dict | None:
    return next((t for t in load() if t["id"] == target_id), None)


def public(t: dict) -> dict:
    """What the admin page gets: everything except the secrets, of which only the names of the ones that are set are listed."""
    secret = backupdrivers.secret_keys(t["type"])
    cfg = t.get("cfg", {})
    st = t.get("state", {})
    return {"id": t["id"], "name": t["name"], "type": t["type"], "schedule": t.get("schedule", "off"), "at": t.get("at", "03:00"), "keep": t.get("keep", 7),
            "cfg": {k: v for k, v in cfg.items() if k not in secret}, "secrets_set": sorted(k for k in secret if cfg.get(k)),
            "state": {"last_run": st.get("last_run", 0), "last_ok": st.get("last_ok", 0), "last_error": st.get("last_error", ""), "last_size": st.get("last_size", 0),
                      "running": t["id"] in _running}}


def _clean_cfg(kind: str, raw, old: dict) -> dict:
    """The settings of a destination: only known fields, numbers as numbers; a secret left empty keeps what was saved before."""
    spec = {f["key"]: f for f in backupdrivers.types()[kind]["fields"]}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, f in spec.items():
        v = raw.get(key)
        if f["type"] == "int":
            try:
                v = int(v if v not in (None, "") else f.get("default", 0))
            except (TypeError, ValueError):
                raise BackupProblem(tr("{field} must be a number.", field=f["label"]))
            if not f.get("min", 0) <= v <= f.get("max", 65535):
                raise BackupProblem(tr("{field} is out of range.", field=f["label"]))
        elif f["type"] == "choice":
            allowed = {c["value"] for c in f["choices"]}
            v = v if v in allowed else f.get("default")
        else:
            v = str(v).strip() if v is not None and f["type"] != "textarea" else (str(v) if v is not None else "")
            if len(v) > 8000:
                raise BackupProblem(tr("{field} is too long.", field=f["label"]))
            if not v and (f["type"] == "password" or f.get("secret")):
                v = old.get(key, "")  # empty = keep
            if not v and f["type"] != "password" and not f.get("secret") and f.get("default") is not None:
                v = f["default"]
        if f.get("required") and v in ("", None):
            raise BackupProblem(tr("{field} is required.", field=f["label"]))
        out[key] = v
    return out


def _validate(data: dict, existing: dict | None) -> dict:
    kind = data.get("type")
    if kind not in backupdrivers.types():
        raise BackupProblem(tr("Unknown kind of destination."))
    name = " ".join(str(data.get("name", "")).split())
    if not 1 <= len(name) <= 40:
        raise BackupProblem(tr("Give the destination a name (1 to 40 characters)."))
    schedule = data.get("schedule", "off")
    if schedule not in SCHEDULES:
        raise BackupProblem(tr("Unknown schedule."))
    at = str(data.get("at", "03:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
        raise BackupProblem(tr("The time must look like 03:30."))
    try:
        keep = int(data.get("keep", 7))
    except (TypeError, ValueError):
        raise BackupProblem(tr("Keep must be a number."))
    if not 1 <= keep <= 365:
        raise BackupProblem(tr("Keep between 1 and 365 backups."))
    old_cfg = existing["cfg"] if existing and existing["type"] == kind else {}
    return {"name": name, "type": kind, "schedule": schedule, "at": at, "keep": keep, "cfg": _clean_cfg(kind, data.get("cfg"), old_cfg)}


def upsert(data: dict) -> dict:
    """Creates a destination or changes the one with data["id"]."""
    with _lock:
        targets = load()
        existing = next((t for t in targets if t["id"] == data.get("id")), None) if data.get("id") else None
        if data.get("id") and not existing:
            raise BackupProblem(tr("That destination does not exist any more."))
        if not existing and len(targets) >= MAX_TARGETS:
            raise BackupProblem(tr("There can be at most {n} destinations.", n=MAX_TARGETS))
        clean = _validate(data, existing)
        if existing:
            if existing["type"] != clean["type"] or existing["cfg"].get("host_key") != clean["cfg"].get("host_key"):
                existing["state"] = {}
            existing.update(clean)
            t = existing
        else:
            t = {"id": "t" + secrets.token_hex(4), **clean, "state": {}}
            targets.append(t)
        _save(targets)
        return t


def remove(target_id: str) -> bool:
    with _lock:
        targets = load()
        left = [t for t in targets if t["id"] != target_id]
        if len(left) == len(targets):
            return False
        _save(left)
    db.resolve_notification(0, f"backup:{target_id}")
    return True


def _update_state(target_id: str, **changes) -> None:
    with _lock:
        targets = load()
        for t in targets:
            if t["id"] == target_id:
                t.setdefault("state", {}).update(changes)
        _save(targets)


def _learn_host_key(target_id: str):
    def learn(fingerprint: str) -> None:
        with _lock:
            targets = load()
            for t in targets:
                if t["id"] == target_id and t["type"] == "sftp" and not t["cfg"].get("host_key"):
                    t["cfg"]["host_key"] = fingerprint
            _save(targets)
    return learn


def driver(t: dict, **kw):
    """The driver for a stored destination (an SFTP server's key is remembered at the first connection)."""
    if t["type"] == "sftp":
        kw.setdefault("learn", _learn_host_key(t["id"]))
    return backupdrivers.make(t["type"], t["cfg"], **kw)


def test(data: dict) -> str:
    """Tries the settings of a destination (saved or not) and says what was found. Raises BackupProblem or BackupError."""
    existing = get(data["id"]) if data.get("id") else None
    clean = _validate({**data, "name": data.get("name") or "x"}, existing)
    kw = {"learn": _learn_host_key(existing["id"])} if existing and clean["type"] == "sftp" else {}
    return backupdrivers.make(clean["type"], clean["cfg"], **kw).test()


# ---------- Making a backup ----------

def new_name(now: float | None = None) -> str:
    return time.strftime("nxtsound-%Y%m%d-%H%M%S.db", time.localtime(now or time.time()))


def _by_age(files: list[dict]) -> list[dict]:
    return sorted(files, key=lambda f: f["name"], reverse=True)  # the names carry the date: newest first


def run(target_id: str) -> dict:
    """Makes a backup and puts it on the destination, then deletes the oldest ones beyond `keep`. Returns what happened; failures are recorded, not raised."""
    t = get(target_id)
    if not t:
        raise BackupProblem(tr("That destination does not exist any more."))
    with _lock:
        if target_id in _running:
            raise BackupProblem(tr("A backup to this destination is already running."))
        _running.add(target_id)
    started = time.time()
    fd, name = tempfile.mkstemp(suffix=".db", prefix="backup-")
    os.close(fd)
    tmp = Path(name)
    try:
        db.backup_to(tmp)
        remote = new_name(started)
        d = driver(t)
        d.put(remote, tmp)
        keep = t.get("keep", 7)
        old = _by_age(d.list())[keep:]
        for f in old:
            try:
                d.delete(f["name"])
            except backupdrivers.BackupError as e:
                log.warning("Backup %s: could not delete the old file %s: %s", t["name"], f["name"], e)
        _update_state(target_id, last_run=started, last_ok=time.time(), last_error="", last_size=tmp.stat().st_size)
        db.resolve_notification(0, f"backup:{target_id}")
        log.info("Backup to %s done (%s, %d old ones deleted)", t["name"], remote, len(old))
        return {"ok": True, "name": remote, "deleted": len(old)}
    except Exception as e:  # noqa: BLE001 the reason goes to the admin, whatever it is
        msg = str(e) if isinstance(e, backupdrivers.BackupError) else tr("Unexpected error: {detail}", detail=e.__class__.__name__)
        if not isinstance(e, backupdrivers.BackupError):
            log.exception("Backup to %s failed", t["name"])
        else:
            log.warning("Backup to %s failed: %s", t["name"], msg)
        _update_state(target_id, last_run=started, last_error=msg)
        db.notify(0, f"backup:{target_id}", NOTE_KIND, {"name": t["name"], "error": msg})
        return {"ok": False, "error": msg}
    finally:
        tmp.unlink(missing_ok=True)
        with _lock:
            _running.discard(target_id)


def start(target_id: str) -> None:
    """Starts a backup in the background (the admin page asks for the state afterwards)."""
    if not get(target_id):
        raise BackupProblem(tr("That destination does not exist any more."))
    if target_id in _running:
        raise BackupProblem(tr("A backup to this destination is already running."))
    threading.Thread(target=run, args=(target_id,), daemon=True, name=f"backup-{target_id}").start()


def files(target_id: str) -> list[dict]:
    t = get(target_id)
    if not t:
        raise BackupProblem(tr("That destination does not exist any more."))
    return _by_age(driver(t).list())


def fetch(target_id: str, name: str) -> Path:
    """Downloads one backup file into a temporary file (the caller deletes it)."""
    t = get(target_id)
    if not t:
        raise BackupProblem(tr("That destination does not exist any more."))
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="fetched-")
    os.close(fd)
    path = Path(tmp)
    try:
        driver(t).get(name, path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def delete_file(target_id: str, name: str) -> None:
    t = get(target_id)
    if not t:
        raise BackupProblem(tr("That destination does not exist any more."))
    driver(t).delete(name)


# ---------- The schedule ----------

def _slot(t: dict, now: datetime.datetime) -> datetime.datetime | None:
    """The latest moment at which a backup was due for this schedule (None: no fixed moment)."""
    h, m = (int(x) for x in t.get("at", "03:00").split(":"))
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t.get("schedule") == "daily":
        return today if now >= today else today - datetime.timedelta(days=1)
    if t.get("schedule") == "weekly":  # on Sundays
        back = (now.weekday() + 1) % 7
        slot = today - datetime.timedelta(days=back)
        return slot if now >= slot else slot - datetime.timedelta(days=7)
    return None


def due(t: dict, now: float | None = None) -> bool:
    """Is a scheduled backup to this destination due? After a failure it is tried again every half hour."""
    schedule = t.get("schedule", "off")
    if schedule == "off" or t["id"] in _running:
        return False
    now = now or time.time()
    st = t.get("state", {})
    last_ok, last_run = st.get("last_ok", 0), st.get("last_run", 0)
    if last_run > last_ok and now - last_run < RETRY_AFTER:
        return False
    if schedule in ("6h", "12h"):
        return now - last_ok >= int(schedule[:-1]) * 3600
    slot = _slot(t, datetime.datetime.fromtimestamp(now))
    return slot is not None and last_ok < slot.timestamp()


def run_due() -> list[str]:
    """Starts what is due (called every minute)."""
    started = []
    for t in load():
        if due(t):
            try:
                start(t["id"])
                started.append(t["id"])
            except BackupProblem:
                pass
    return started


async def loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_due)
        except Exception:  # noqa: BLE001 a broken round must not end the schedule
            log.exception("Backup schedule")
        await asyncio.sleep(60)
