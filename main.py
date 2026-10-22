"""
Self-hosted music server (FastAPI backend) with a web UI.

- Your own music files from a folder (scan with tags and covers), search, artist and album pages
- Login with name and password, playlists, likes, listening and search history in SQLite
- All API routes except login/logout and /api/server-info require a valid session
- Home page with recommendations from the listening history, DJ analysis (tempo, key) for the web player
"""
import asyncio
import hashlib
import hmac
import json
import subprocess
import shutil
import tempfile
from contextlib import asynccontextmanager
import mimetypes
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.datastructures import MutableHeaders
from pydantic import BaseModel, Field, field_validator

import bpm as bpmlib
import autolists
import db
import devices
import enrich
import i18n
import logconf
import loginalerts
import pluginhost
import plugins
import library
import pluginproto
import sysmon
from i18n import tr

log = logging.getLogger("server")
pluginproto.translate = tr  # plugin errors are translated into the language of the current request


def _read_version() -> dict:
    base = Path(__file__).parent
    try:
        version = (base / "VERSION").read_text().strip()
    except OSError:
        version = "0.0.0"
    commit = os.getenv("GIT_COMMIT", "").strip()
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=base, capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    return {"version": version, "commit": commit[:12] or "unknown"}


VERSION_INFO = _read_version()

BASE = Path(__file__).parent
APP_NAME = os.getenv("APP_NAME", "NxtSound")  # central display name
API_VERSION = 1  # version of the HTTP API (and the plugin interface), used when linking the app
SESSION_TTL = int(float(os.getenv("SESSION_DAYS", "30")) * 86400)  # a session lasts this long without use; every use extends it
SESSION_MAX_AGE = int(float(os.getenv("SESSION_MAX_DAYS", "180")) * 86400)  # ... but never longer than this after the login (0 = no limit)
TRUST_PROXY = os.getenv("TRUST_PROXY", "0").lower() in ("1", "true", "yes")  # only behind a reverse proxy that sets X-Forwarded-* itself
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "auto").lower()  # auto | true | false
ALLOWED_ORIGINS = {o.strip().lower().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()}
MAX_BODY = 2 * 1024 * 1024  # largest allowed request body (JSON), in bytes
db.init_db()
db.purge_sessions(SESSION_TTL, SESSION_MAX_AGE)
logconf.apply()


def _bootstrap_admin() -> None:
    """Docker: ADMIN_USER and ADMIN_PASSWORD create the admin on first start (only if the database is empty)."""
    name, pw = os.getenv("ADMIN_USER", "").strip(), os.getenv("ADMIN_PASSWORD", "")
    if not name and not pw:
        return
    if db.count_users() > 0:
        return
    name = " ".join(name.split())
    if not NAME_RE.match(name) or len(pw) < PW_MIN:
        log.warning("ADMIN_USER/ADMIN_PASSWORD invalid (name 2-32 characters, password at least %d): admin not created", PW_MIN)
        return
    db.create_user(name, pw, is_admin=True)
    log.info("Admin '%s' created from ADMIN_USER/ADMIN_PASSWORD", name)


NAME_RE = re.compile(r"^[\w .\-]{2,32}$")
PW_MIN = 8
_bootstrap_admin()
SESSION_COOKIE = "session"

mimetypes.add_type("application/manifest+json", ".webmanifest")

SCAN_ON_START = os.getenv("SCAN_ON_START", "1") != "0"
PLUGINS_ON_START = os.getenv("PLUGINS_ON_START", "1") != "0"
SYSMON_ON_START = os.getenv("SYSMON_ON_START", "1") != "0"  # the background checks (disk space, new versions)
SCAN_MINUTES = float(os.getenv("SCAN_MINUTES", "0"))  # 0 = only at startup and via the admin button


async def _scan_loop() -> None:
    while True:
        await asyncio.sleep(SCAN_MINUTES * 60)
        library.start_scan()


@asynccontextmanager
async def _lifespan(_app):
    task = None
    log.info("NxtSound server %s started (default language %s, plugins directory %s)", VERSION_INFO["version"], i18n.DEFAULT_LANG, pluginhost.PLUGINS_DIR)
    if (problem := pluginhost.dir_problem()):
        log.warning("Plugins cannot be installed: %s", problem)
    if SCAN_ON_START:
        library.start_scan()
    if PLUGINS_ON_START:
        threading.Thread(target=pluginhost.start_all, daemon=True, name="plugins-start").start()
    if SCAN_MINUTES > 0:
        task = asyncio.create_task(_scan_loop())
    mon = asyncio.create_task(sysmon.loop()) if SYSMON_ON_START else None
    import_stop = threading.Event()
    threading.Thread(target=library.watch_imports, args=(import_stop,), daemon=True, name="import-watch").start()
    try:
        yield
    finally:
        import_stop.set()
        log.info("NxtSound server stopping")
        pluginhost.shutdown()
        for t in (task, mon):
            if t:
                t.cancel()


app = FastAPI(title=APP_NAME, lifespan=_lifespan)


@app.exception_handler(Exception)
async def _unexpected_error(request: Request, exc: Exception):
    """Unexpected errors answer with JSON and a reference; the details (traceback) stay in the log, not in the answer: they may name paths or queries."""
    ref = secrets.token_hex(4)
    log.error("Unexpected error %s on %s %s", ref, request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": tr("Unexpected error (reference {ref}, see the server log).", ref=ref)}, status_code=500)

# ---------- Helpers ----------

TRACK_ID_RE = re.compile(r"^[a-z0-9_-]{2,32}:[A-Za-z0-9_.-]{1,64}$")  # <source>:<id>, e.g. local:<uuid>
BROWSE_ID_RE = re.compile(r"^(?:[A-Za-z0-9_-]{5,64}|[a-z][a-z0-9-]{1,23}:[A-Za-z0-9_.-]{1,40})$")  # local IDs or <plugin>:<id>
SEARCH_KINDS = {"songs", "artists", "albums"}


def _check_id(track_id: str) -> str:
    if not TRACK_ID_RE.match(track_id):
        raise HTTPException(400, tr("Invalid track_id"))
    return track_id


def _check_browse(value: str) -> str:
    if not BROWSE_ID_RE.match(value):
        raise HTTPException(400, tr("Invalid id"))
    return value


# ---------- Models and auth ----------

class Track(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{2,32}:[A-Za-z0-9_.-]{1,64}$")
    title: str = Field("", max_length=300)
    artist: str = Field("", max_length=300)
    artist_id: str = Field("", max_length=64)
    album: str = Field("", max_length=300)
    album_id: str = Field("", max_length=64)
    duration: str = Field("", max_length=16)
    thumb: str = Field("", max_length=500)

    @field_validator("thumb")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return v if v.startswith(("https://", "/api/cover/")) else ""


class LoginIn(BaseModel):
    name: str = Field(max_length=100)
    password: str = Field(max_length=200)


class NewUserIn(BaseModel):
    name: str = Field(max_length=100)
    password: str = Field(min_length=PW_MIN, max_length=200)
    is_admin: bool = False


class UserPatch(BaseModel):
    password: str | None = Field(default=None, min_length=PW_MIN, max_length=200)
    is_admin: bool | None = None


class PasswordIn(BaseModel):
    old: str = Field(max_length=200)
    new: str = Field(min_length=PW_MIN, max_length=200)


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tracks: list[Track] = Field(default_factory=list, max_length=500)


class PlaylistRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TracksIn(BaseModel):
    tracks: list[Track] = Field(max_length=500)


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_https(scheme: str, headers) -> bool:
    if scheme == "https":
        return True
    return TRUST_PROXY and headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def _client_ip(request: Request) -> str:
    """Client IP for login throttling. Behind a trusted proxy its entry counts (rightmost X-Forwarded-For value)."""
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    secure = COOKIE_SECURE == "true" or (COOKIE_SECURE == "auto" and _is_https(request.url.scheme, request.headers))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure, path="/")


def current_user(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    user = db.user_for_token(token, SESSION_TTL, SESSION_MAX_AGE, _client_ip(request)) if token else None
    if not user:
        raise HTTPException(401, tr("Not logged in"))
    if request.method not in SAFE_METHODS:  # CSRF protection: state-changing calls need the session's token in the header
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf"]):
            raise HTTPException(403, tr("CSRF token missing or invalid"))
    if user.pop("_renew", False):
        _set_session_cookie(request, response, token)  # extend the cookie lifetime along with the session
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, tr("Admins only"))
    return user


api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])  # everything behind the login
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])


def _valid_user_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not NAME_RE.match(name):
        raise HTTPException(400, tr("Name: 2 to 32 characters (letters, digits, spaces, dot, underscore, hyphen)"))
    return name


_login_fails: dict[tuple[str, str], list[float]] = {}  # (ip, name | "*") -> times of failed attempts
LOGIN_MAX_FAILS = 5        # per IP and name
LOGIN_MAX_FAILS_IP = 20    # per IP across all names
LOGIN_WINDOW = 300.0


def _throttle_check(request: Request, name: str) -> None:
    ip, now = _client_ip(request), time.time()
    if len(_login_fails) > 5000:  # bound memory use
        for k in [k for k, v in _login_fails.items() if not v or now - v[-1] > LOGIN_WINDOW]:
            _login_fails.pop(k, None)
    for key, limit in (((ip, name.lower()), LOGIN_MAX_FAILS), ((ip, "*"), LOGIN_MAX_FAILS_IP)):
        fails = [t for t in _login_fails.get(key, []) if now - t < LOGIN_WINDOW]
        _login_fails[key] = fails
        if len(fails) >= limit:
            wait = int(LOGIN_WINDOW - (now - fails[0])) + 1
            raise HTTPException(429, tr("Too many attempts. Please try again in {wait} s.", wait=wait))


def _record_fail(request: Request, name: str) -> None:
    ip, now = _client_ip(request), time.time()
    for key in ((ip, name.lower()), (ip, "*")):
        _login_fails.setdefault(key, []).append(now)


def _start_session(request: Request, response: Response, user: dict) -> str:
    ua, ip = devices.short_agent(request.headers.get("user-agent", "")), _client_ip(request)
    token, csrf = db.create_session(user["id"], ua, ip)
    loginalerts.on_login(user["id"], ip, ua)
    _set_session_cookie(request, response, token)
    return csrf


def _public(user: dict) -> dict:
    out = {
        "id": user["id"], "name": user["name"], "is_admin": user["is_admin"], "onboarded": user["onboarded"],
        "must_change_password": user["must_change_password"], "lang": user.get("lang", ""),
    }
    return out


def _suggest_names(base: str, except_id: int | None = None, n: int = 3) -> list[str]:
    """Free alternatives to a taken name."""
    base = base.strip()[:28].rstrip(" .-_") or "user"
    out: list[str] = []
    for suffix in ("2", "_2", "1", "42", "_1", "7", "99", "3", ".x", "_music"):
        cand = (base + suffix)[:32]
        if NAME_RE.match(cand) and not db.name_taken(cand, except_id) and cand not in out:
            out.append(cand)
        if len(out) >= n:
            break
    return out


@api.get("/name-check")
def name_check(name: str = Query("", max_length=100), new: bool = False, user: dict = Depends(current_user)):
    """Live check while typing: valid, free, or taken (with suggestions)."""
    clean = " ".join(name.split())
    if not clean:
        return {"state": "empty"}
    if not NAME_RE.match(clean):
        return {"state": "invalid", "message": tr("2 to 32 characters: letters, digits, spaces, dot, underscore, hyphen")}
    own = None if new else user["id"]  # new=1: name for a new account, where the own name counts as taken too
    if own and clean == user["name"]:
        return {"state": "same", "message": tr("That is your current name")}
    if db.name_taken(clean, except_id=own):
        return {"state": "taken", "message": tr("Name is already taken"), "suggestions": _suggest_names(clean, own)}
    return {"state": "free", "message": tr("Name is available")}


@app.get("/api/auth-state")
def auth_state():
    """For the login screen: first start (create the admin) or an admin without password (old update)."""
    return {"setup": db.count_users() == 0, "claim": db.claimable_admin()}


@app.post("/api/setup")
def setup(body: LoginIn, request: Request, response: Response):
    """Creates the admin on the very first start. Locked afterwards; only the admin creates further users."""
    if db.count_users() > 0:
        raise HTTPException(403, tr("Setup already completed"))
    name = _valid_user_name(body.name)
    if len(body.password) < PW_MIN:
        raise HTTPException(400, tr("Password: at least {n} characters", n=PW_MIN))
    user = db.create_user(name, body.password, is_admin=True)
    if user is None:
        raise HTTPException(409, tr("Name already taken"))
    return {"user": _public(user), "csrf": _start_session(request, response, user)}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response):
    name = " ".join(body.name.split())
    _throttle_check(request, name)
    pending = db.find_user(name)
    if pending and pending["is_admin"] and not pending["has_password"] and len(body.password) < PW_MIN:
        raise HTTPException(400, tr("Password: at least {n} characters", n=PW_MIN))
    user = db.check_login(name, body.password)
    if user is None:
        _record_fail(request, name)
        raise HTTPException(401, tr("Wrong name or password"))
    _login_fails.pop((_client_ip(request), name.lower()), None)
    return {"user": _public(user), "csrf": _start_session(request, response, user)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@api.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": _public(user), "csrf": user["csrf"]}


# ---------- Devices: the user's own logins ----------

class SessionNameIn(BaseModel):
    name: str = Field(max_length=db.SESSION_NAME_MAX)


@api.get("/me/sessions")
def my_sessions(request: Request, user: dict = Depends(current_user)):
    """The user's logins ("devices"): name, browser, system, IP at login and at last use, since when, last used. The current one is marked."""
    out = []
    for s in db.list_sessions(user["id"], request.cookies.get(SESSION_COOKIE, "")):
        browser, system = devices.describe(s.pop("ua"))
        out.append({**s, "browser": browser, "system": system})
    return {"sessions": out}


@api.patch("/me/sessions/{session_id}")
def rename_my_session(session_id: int, body: SessionNameIn, user: dict = Depends(current_user)):
    if not db.rename_session(user["id"], session_id, body.name):
        raise HTTPException(404, tr("Device not found"))
    return {"ok": True}


@api.delete("/me/sessions/{session_id}")
def end_my_session(session_id: int, request: Request, user: dict = Depends(current_user)):
    """Logs another device out at once. The device asking uses the normal log out."""
    result = db.end_session(user["id"], session_id, request.cookies.get(SESSION_COOKIE, ""))
    if result == "missing":
        raise HTTPException(404, tr("Device not found"))
    if result == "current":
        raise HTTPException(400, tr("This is the device you are using. Use Log out instead."))
    return {"ok": True}


@api.delete("/me/sessions")
def end_my_other_sessions(request: Request, user: dict = Depends(current_user)):
    """Logs out everywhere except on the device asking."""
    return {"ended": db.end_other_sessions(user["id"], request.cookies.get(SESSION_COOKIE, ""))}


class ListenIn(BaseModel):
    seconds: float = Field(ge=0, le=300)


def _bytes(n: float) -> str:
    return f"{n / 1024**3:.1f} GB" if n >= 1024**3 else f"{n / 1024**2:.0f} MB"


def _render_notification(n: dict) -> dict:
    """Title, text and target of a message, in the language of the reader."""
    d, kind = n["data"], n["kind"]
    title, body, nav = tr("Notification"), "", ""
    if kind == "disk_low":
        title = tr("Disk space is running low")
        body = tr("{what}: {free} GB of {total} GB free ({pct} %)", what=tr("Music folder") if d.get("name") == "music" else tr("Data folder"), free=d.get("free_gb"), total=d.get("total_gb"), pct=d.get("percent"))
        nav = "#/admin"
    elif kind == "plugin_update":
        title = tr("Plugin update available: {name}", name=d.get("name", d.get("id", "")))
        body = tr("Version {version}", version=d["version"]) if d.get("version") else tr("A newer version is in the repository")
        nav = "#/admin/plugins"
    elif kind == "server_update":
        title = tr("Beta available: {version}", version=d.get("latest", "")) if d.get("prerelease") else tr("Server update available: {version}", version=d.get("latest", ""))
        body = tr("This server runs {current}.", current=d.get("current", ""))
        nav = "#/admin/system"
    elif kind == "follow":
        title = tr("{name} follows you", name=d.get("name", ""))
        nav = "#/community"
    elif kind == "new_login":
        title = tr("New login to your account")
        device = tr("{browser} on {system}", browser=d["browser"], system=d["system"]) if d.get("browser") and d.get("system") else d.get("browser") or d.get("system") or tr("Unknown device")
        if d.get("new_device") and d.get("new_network"):
            body = tr("{device} ({ip}): a device and a network you have not used before.", device=device, ip=d.get("ip", ""))
        elif d.get("new_device"):
            body = tr("{device} ({ip}): a device you have not used before.", device=device, ip=d.get("ip", ""))
        else:
            body = tr("{device} ({ip}): a network you have not used before.", device=device, ip=d.get("ip", ""))
        nav = "#/account/devices"
    return {"id": n["id"], "kind": kind, "system": n["system"], "title": title, "body": body, "nav": nav, "created_at": n["created_at"], "unread": n["unread"]}


@api.get("/notifications")
def notifications(user: dict = Depends(current_user)):
    """The bell: the user's messages and, for admins, the system messages (disk space, updates). Newest first."""
    d = db.notifications_for(user["id"], user["is_admin"])
    return {"unread": d["unread"], "items": [_render_notification(n) for n in d["items"]]}


@api.post("/notifications/seen")
def notifications_seen(user: dict = Depends(current_user)):
    db.mark_notifications_seen(user["id"])
    return {"ok": True}


@api.delete("/notifications/{notification_id}")
def notification_dismiss(notification_id: int, user: dict = Depends(current_user)):
    if not db.dismiss_notification(user["id"], user["is_admin"], notification_id):
        raise HTTPException(404, tr("Not found"))
    return {"ok": True}


# ---------- Community ----------

def _community_or_403() -> None:
    if not db.community_enabled():
        raise HTTPException(403, tr("The community feature is turned off."))


@api.get("/community")
def community_status(user: dict = Depends(current_user)):
    """Is the community feature on, and the user's own state in it."""
    if not db.community_enabled():
        return {"enabled": False}
    return {"enabled": True, "share": db.get_share(user["id"]), "following": db.following_ids(user["id"]), "followers": db.follower_count(user["id"])}


@api.get("/community/people")
def community_people(user: dict = Depends(current_user)):
    _community_or_403()
    return {"people": db.people(user["id"])}


@api.post("/community/follow/{other_id}")
def community_follow(other_id: int, user: dict = Depends(current_user)):
    _community_or_403()
    if other_id == user["id"]:
        raise HTTPException(400, tr("You cannot follow yourself."))
    new = db.follow(user["id"], other_id)
    if not new and other_id not in db.following_ids(user["id"]):
        raise HTTPException(404, tr("User not found"))
    if new:
        db.notify(other_id, f"follow:{user['id']}", "follow", {"user_id": user["id"], "name": user["name"]})
    return {"ok": True, "following": True}


@api.delete("/community/follow/{other_id}")
def community_unfollow(other_id: int, user: dict = Depends(current_user)):
    _community_or_403()
    db.unfollow(user["id"], other_id)
    db.resolve_notification(other_id, f"follow:{user['id']}")
    return {"ok": True, "following": False}


class ShareIn(BaseModel):
    share: bool


@api.put("/community/share")
def community_share(body: ShareIn, user: dict = Depends(current_user)):
    """Whether the people who follow the user see what they listen to (off by default)."""
    _community_or_403()
    db.set_share(user["id"], body.share)
    return {"share": body.share}


async def _friends(user_id: int) -> list[dict]:
    """The friends' recent plays that can still be played here (the song is in the library or its plugin is active)."""
    feed = await asyncio.to_thread(db.friends_feed, user_id)
    ids = [f["track"]["id"] for f in feed]
    alive = await asyncio.to_thread(library.existing, ids)
    alive |= {i for i in ids if plugins.is_enabled_id(i)}
    return [f for f in feed if f["track"]["id"] in alive]


@api.get("/community/feed")
async def community_feed(user: dict = Depends(current_user)):
    _community_or_403()
    return {"items": await _friends(user["id"])}


class CommunityIn(BaseModel):
    enabled: bool


@admin.get("/community")
def admin_community():
    return {"enabled": db.community_enabled(), "users": db.count_users(), "sharing": sum(1 for p in db.people(0) if p["shares"])}


class LoginAlertsIn(BaseModel):
    enabled: bool


@admin.get("/login-alerts")
def admin_login_alerts():
    """Whether users are warned about logins from a new device or network."""
    return {"enabled": loginalerts.enabled(), "default": loginalerts.DEFAULT_ON}


@admin.put("/login-alerts")
def admin_login_alerts_set(body: LoginAlertsIn):
    loginalerts.set_enabled(body.enabled)
    log.info("Login alerts turned %s", "on" if body.enabled else "off")
    return {"enabled": body.enabled, "default": loginalerts.DEFAULT_ON}


@admin.put("/community")
def admin_community_set(body: CommunityIn):
    """Turns the community feature on or off for everyone. Follows and settings stay when it is turned off."""
    db.set_community_enabled(body.enabled)
    log.info("Community feature turned %s", "on" if body.enabled else "off")
    _home_cache.clear()
    return {"enabled": body.enabled}


@api.get("/me/offline-set")
async def offline_set(recent: int = Query(20, ge=0, le=50), top: int = Query(20, ge=0, le=50), user: dict = Depends(current_user)):
    """The songs the app may keep for playing without a connection: the last played and the most played ones of this user, each once, newest
    plays first. Only what can be played as a file: not songs that are gone, and not plugin sources that deliver live streams or episodes."""
    uid = user["id"]
    wanted: dict[str, dict] = {}
    for t in await asyncio.to_thread(db.recent_tracks, uid, recent) + await asyncio.to_thread(db.top_tracks, uid, top):
        wanted.setdefault(t["id"], t)
    ids = list(wanted)
    alive = await asyncio.to_thread(library.existing, ids)
    streamy = {p["id"] for p in plugins.list_plugins() if p["categories"]}  # sources with their own search tabs (radio, podcasts): not for keeping
    alive |= {i for i in ids if plugins.is_enabled_id(i) and i.split(":")[0] not in streamy}
    blocked = await asyncio.to_thread(db.blocked_sets, uid)
    tracks = _allowed([t for t in wanted.values() if t["id"] in alive], blocked)
    return {"tracks": tracks}


@api.get("/me/recap")
def my_recap(period: Literal["week", "month", "year", "all"] = "month", tz: int = Query(0, ge=-840, le=840), user: dict = Depends(current_user)):
    """The user's own listening numbers for a period (last 7 days, 30 days, a year, or everything). tz: minutes the clock is ahead of UTC."""
    out = db.recap(user["id"], period, tz)
    out["sources"] = [{"source": s["source"], "name": next((x["name"] for x in plugins.sources() if x["id"] == s["source"]), s["source"]), "plays": s["plays"]} for s in out["sources"]]
    return out


@api.post("/listen")
def listen(body: ListenIn, user: dict = Depends(current_user)):
    """The player reports seconds listened (in small batches) so the dashboard knows the real listening time."""
    db.add_listening(user["id"], body.seconds)
    return {"ok": True}


@api.post("/me/onboarded")
def me_onboarded(user: dict = Depends(current_user)):
    db.mark_onboarded(user["id"])
    return {"ok": True}


class NameIn(BaseModel):
    name: str = Field(max_length=100)


@api.put("/me/name")
def me_name(body: NameIn, user: dict = Depends(current_user)):
    name = _valid_user_name(body.name)
    if name != user["name"] and not db.rename_user(user["id"], name):
        raise HTTPException(409, tr("Name is already taken"))
    return {"name": name}


class LangIn(BaseModel):
    lang: Literal["", "en", "de"]  # "" = automatic (browser language)


@api.put("/me/lang")
def me_lang(body: LangIn, user: dict = Depends(current_user)):
    db.set_lang(user["id"], body.lang)
    return {"lang": body.lang}


@api.put("/me/password")
def me_password(body: PasswordIn, request: Request, user: dict = Depends(current_user)):
    _throttle_check(request, user["name"])
    if db.check_login(user["name"], body.old) is None:
        _record_fail(request, user["name"])
        raise HTTPException(403, tr("Old password is wrong"))
    db.update_user(user["id"], password=body.new)
    return {"ok": True}  # all sessions are gone, the client logs in again


# ---------- User management (admin only) ----------

@admin.get("/users")
def admin_users():
    return {"users": [{**u, "pw_hash": None} for u in db.list_users()]}


@admin.post("/users")
def admin_user_add(body: NewUserIn):
    name = _valid_user_name(body.name)
    u = db.create_user(name, body.password, is_admin=body.is_admin)
    if u is None:
        raise HTTPException(409, tr("Name already taken"))
    return {"user": u}


@admin.patch("/users/{user_id}")
def admin_user_patch(user_id: int, body: UserPatch, me_: dict = Depends(current_user)):
    if body.is_admin is False and user_id == me_["id"]:
        raise HTTPException(400, tr("You cannot remove your own admin rights"))
    if not db.update_user(user_id, password=body.password, is_admin=body.is_admin):
        raise HTTPException(404, tr("User not found"))
    return {"ok": True}


@admin.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: int):
    """Sets a random one-time password. The user is logged out and must choose a new one at the next login."""
    pw = secrets.token_urlsafe(9)
    if not db.update_user(user_id, password=pw, temp=True):
        raise HTTPException(404, tr("User not found"))
    return {"password": pw}


@admin.delete("/users/{user_id}")
def admin_user_delete(user_id: int, me_: dict = Depends(current_user)):
    if user_id == me_["id"]:
        raise HTTPException(400, tr("You cannot delete yourself"))
    target = next((u for u in db.list_users() if u["id"] == user_id), None)
    if target is None:
        raise HTTPException(404, tr("User not found"))
    db.delete_user(user_id)
    return {"ok": True}


@admin.get("/backup")
def admin_backup():
    """Consistent download of the database (users, playlists, history). Contains password hashes, no sessions."""
    fd, name = tempfile.mkstemp(suffix=".db", prefix="backup-")
    os.close(fd)
    tmp = Path(name)
    try:
        db.backup_to(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    fname = time.strftime("backup-%Y%m%d-%H%M.db")
    return FileResponse(tmp, media_type="application/octet-stream", filename=fname, background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


MAX_RESTORE_BYTES = 1 << 30


async def _save_upload(request: Request, limit: int, suffix: str) -> Path:
    """Stores an uploaded file in a temporary file and returns its path (the caller deletes it).

    Accepts `multipart/form-data` with the file in the field `file` (what browsers send for uploads; reverse proxies and web
    application firewalls let it through) or a raw request body (for scripts, e.g. curl --data-binary)."""
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="upload-")
    os.close(fd)
    tmp = Path(name)
    try:
        size = 0
        with open(tmp, "wb") as f:
            if request.headers.get("content-type", "").startswith("multipart/form-data"):
                form = await request.form()
                part = form.get("file")
                if part is None or not hasattr(part, "read"):
                    raise HTTPException(400, tr("No file received"))
                while chunk := await part.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, tr("File too large"))
                    f.write(chunk)
                await form.close()
            else:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, tr("File too large"))
                    f.write(chunk)
        return tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@admin.post("/restore")
async def admin_restore(request: Request):
    """Restores a backup (uploaded file). Replaces users, playlists, history and settings; a copy of the current state is
    saved in the data folder first. All sessions end, the library is rescanned."""
    tmp = await _save_upload(request, MAX_RESTORE_BYTES, ".db")
    try:
        err = await asyncio.to_thread(db.check_backup, tmp)
        if err:
            raise HTTPException(400, err)
        safety = db.DB_PATH.parent / time.strftime("before-restore-%Y%m%d-%H%M%S.db")
        await asyncio.to_thread(db.backup_to, safety)
        await asyncio.to_thread(db.restore_from, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    _home_cache.clear()
    log.info("Backup restored (previous state saved as %s)", safety.name)
    library.start_scan(full=True)
    return {"ok": True, "safety": safety.name}


# ---------- Search, radio, streaming ----------

class StartStat(BaseModel):
    ms: int = Field(ge=0, le=300000)
    platform: Literal["ios", "desktop"]


@api.post("/startstat")
def start_stat(body: StartStat, user: dict = Depends(current_user)):
    """Time from tap until sound plays, measured on the device (shows where the time really goes: server or transfer)."""
    db.add_stream_stat("client_" + body.platform, body.ms)
    return {"ok": True}


# ---------- Server info (no login, used to link the app) ----------

@app.get("/api/server-info")
def server_info():
    return {
        "server": "nxtsound",  # marker by which the app recognizes a real NxtSound server (not an arbitrary website)
        "name": APP_NAME,
        "version": VERSION_INFO["version"],
        "api_version": API_VERSION,
        "functions": ["library", "search", "playlists", "likes", "history", "recommendations", "dj_analysis", "lyrics", "plugins"],
        "login_methods": ["password"],
        "setup_required": db.count_users() == 0,
    }


# ---------- Library: search, radio, streaming, covers ----------

AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
               ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff"}


@api.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), kind: str = "songs", source: str = "all", user: dict = Depends(current_user)):
    """Own library first, then results from active plugins. source=local: library only, source=plugins: all plugins only
    (the UI fetches both separately so the library shows up immediately), source=<plugin>: that plugin only."""
    extra = {c["id"] for c in plugins.categories()}
    if kind not in SEARCH_KINDS and kind not in extra:
        raise HTTPException(400, tr("kind must be one of {kinds}", kinds=sorted(SEARCH_KINDS | extra)))
    items = []
    if source in ("all", "local") and kind in SEARCH_KINDS:  # plugin categories have no library part
        fn = {"songs": library.search_tracks, "artists": library.search_artists, "albums": library.search_albums}[kind]
        items = await asyncio.to_thread(fn, q)
        if kind == "songs":
            items = [{**t, "type": "track"} for t in items]
    if source != "local":  # all/plugins: every active plugin, otherwise only the named one
        items = items + await asyncio.to_thread(plugins.search, q, kind, "" if source in ("all", "plugins") else source)
    if source != "plugins":  # the UI asks twice (library, plugins): record the search history only once
        await asyncio.to_thread(db.record_search, user["id"], q)
    return {"kind": kind, "items": items}


@api.get("/radio/{track_id}")
async def radio(track_id: str, limit: int = Query(40, ge=1, le=100), dj: bool = False, user: dict = Depends(current_user)):
    """Similar tracks; with dj=1 ordered so that tempo and key of consecutive tracks fit (as far as analyzed)."""
    _check_id(track_id)
    blocked = await asyncio.to_thread(db.blocked_sets, user["id"])
    if plugins.is_plugin_id(track_id):
        try:
            return {"tracks": _allowed(await asyncio.to_thread(plugins.radio, track_id, limit), blocked)}
        except plugins.PluginError:
            return {"tracks": []}
    tracks = _allowed(await asyncio.to_thread(library.similar, track_id, limit), blocked)
    if dj:
        tracks = await asyncio.to_thread(library.dj_order, tracks, track_id)
    return {"tracks": tracks}


@api.get("/stream/{track_id}")
def stream(track_id: str, request: Request):
    _check_id(track_id)
    if plugins.is_plugin_id(track_id):  # plugin track: pass the audio through from the plugin (Range is forwarded)
        try:
            status, headers, body = plugins.open_stream(track_id, request.headers.get("range", ""))
        except plugins.PluginError:
            raise HTTPException(404, tr("Track not found"))
        return StreamingResponse(body, status_code=status, headers=headers)
    path = library.file_path(track_id)
    if path is None:
        raise HTTPException(404, tr("Track not found"))
    return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"))  # Starlette serves Range requests


class PrefetchIn(BaseModel):
    ids: list[str] = Field(max_length=plugins.MAX_PREFETCH_IDS)


@api.post("/prefetch")
async def prefetch(body: PrefetchIn):
    """The player tells which tracks are queued next; plugins may fetch them ahead of time (the plugin decides how many). Best effort."""
    ids = [i for i in body.ids if len(i) <= 64 and plugins.is_plugin_id(i)]
    sent = await asyncio.to_thread(plugins.prefetch, ids) if ids else 0
    return {"sent": sent}


@api.get("/nowplaying/{track_id}")
async def now_playing(track_id: str):
    """The song a live stream (radio) plays right now, if the plugin knows it: {"title", "artist"}, else {}. The player asks about every 20 s."""
    _check_id(track_id)
    if not plugins.is_plugin_id(track_id):
        return {}
    try:
        return await asyncio.to_thread(plugins.now_playing, track_id)
    except plugins.PluginError:
        return {}


@api.get("/cover/{track_id}")
def cover(track_id: str):
    _check_id(track_id)
    path = library.cover_path(track_id)
    if path is None:
        raise HTTPException(404, tr("No cover"))
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@api.get("/suggest")
async def suggest(q: str = Query(min_length=1, max_length=100), source: str = "all"):
    """Search suggestions while typing. source=local (instant, from the library), plugins (suggestions from the sources, may take a while) or all."""
    out: list[str] = []
    if source in ("all", "local"):
        out = await asyncio.to_thread(library.suggest, q)
    if source in ("all", "plugins"):
        extra = await asyncio.to_thread(plugins.suggest, q)
        have = {x.lower() for x in out}
        out = out + [x for x in extra if x.lower() not in have]
    return {"suggestions": out}


@api.get("/search-categories")
def search_categories():
    """Extra search tabs from plugins (e.g. live radio, podcasts) next to songs, artists and albums."""
    return {"categories": plugins.categories()}


@api.get("/sources")
def sources():
    """Active sources (library and plugins) with display names so the UI can show a track's origin subtly."""
    return {"sources": plugins.sources()}


# ---------- Artists and albums ----------

@api.get("/artist/{artist_id}")
async def artist(artist_id: str, name: str = Query("", max_length=100)):
    _check_browse(artist_id)
    if plugins.is_plugin_id(artist_id):
        try:
            return await asyncio.to_thread(plugins.artist_page, artist_id)
        except plugins.PluginError:
            raise HTTPException(404, tr("Artist not found"))
    page = await asyncio.to_thread(library.artist_page, artist_id)
    if page is None:
        raise HTTPException(404, tr("Artist not found"))
    return page


@api.get("/album/{browse_id}")
async def album(browse_id: str):
    _check_browse(browse_id)
    if plugins.is_plugin_id(browse_id):
        try:
            return await asyncio.to_thread(plugins.album_page, browse_id)
        except plugins.PluginError:
            raise HTTPException(404, tr("Album not found"))
    page = await asyncio.to_thread(library.album_page, browse_id)
    if page is None:
        raise HTTPException(404, tr("Album not found"))
    return page


# ---------- Admin: library and dashboard ----------

@admin.get("/library")
def admin_library():
    return library.stats()


@admin.post("/library/scan")
def admin_library_scan(full: bool = False):
    library.start_scan(full)
    return library.stats()


class PluginIn(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    token: str = Field("", max_length=200)


class PluginEnabledIn(BaseModel):
    enabled: bool


def _plugin_view(p: dict) -> dict:
    if p["kind"] == "managed":
        p = {**p, **pluginhost.status(p["id"]), "update": pluginhost.UPDATES.get(p["id"], {"supported": p["source"] not in ("", "upload")})}
    return p


class PluginRepoIn(BaseModel):
    repo: str = Field(min_length=8, max_length=300)
    ref: str = Field("", max_length=100)


@admin.get("/plugins")
def admin_plugins():
    return {"plugins": [_plugin_view(p) for p in plugins.list_plugins()], "api_version": plugins.API_VERSION,
            "repo_install": pluginhost.REPO_INSTALL, "upload_install": pluginhost.UPLOAD_INSTALL, "dir_problem": pluginhost.dir_problem(), "plugins_dir": str(pluginhost.PLUGINS_DIR), "invalid": pluginhost.invalid_folders()}


@admin.post("/plugins")
def admin_plugin_add(body: PluginIn):
    """Register a remote plugin (separate service): the server fetches and checks its manifest. Only admins manage plugins."""
    try:
        return plugins.add(body.url.strip(), body.token.strip())
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


@admin.post("/plugins/upload")
async def admin_plugin_upload(request: Request, replace: str = Query("", max_length=24)):
    """Install a plugin from an uploaded ZIP. With ?replace=<id> a plugin installed by upload gets a new version."""
    if not pluginhost.UPLOAD_INSTALL:
        raise HTTPException(400, tr("Installation by ZIP upload is turned off on this server."))
    tmp = await _save_upload(request, pluginhost.MAX_ZIP_BYTES, ".zip")
    try:
        return _plugin_view(await asyncio.to_thread(pluginhost.install_zip, tmp, replace.strip()))
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())
    finally:
        tmp.unlink(missing_ok=True)


@admin.post("/plugins/install")
async def admin_plugin_install(body: PluginRepoIn):
    """Install a plugin from a git repository (https); the server sets it up and starts it in the background."""
    try:
        return _plugin_view(await asyncio.to_thread(pluginhost.install_repo, body.repo, body.ref.strip()))
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


@admin.patch("/plugins/{plugin_id}")
def admin_plugin_set(plugin_id: str, body: PluginEnabledIn):
    if not plugins.set_enabled(plugin_id, body.enabled):
        raise HTTPException(404, tr("Plugin not found"))
    p = plugins.get(plugin_id)
    if p["kind"] == "managed":
        (pluginhost.start if body.enabled else pluginhost.stop)(plugin_id)
    return _plugin_view(p)


@admin.get("/plugins/{plugin_id}/log")
def admin_plugin_log(plugin_id: str, limit: int = Query(200, ge=1, le=300)):
    """The latest messages of one plugin: what it printed and what the server said about it, oldest first."""
    if not plugins.get(plugin_id):
        raise HTTPException(404, tr("Plugin not found"))
    return {"lines": logconf.recent_for_plugin(plugin_id, limit)}


@admin.post("/plugins/{plugin_id}/check-update")
async def admin_plugin_check_update(plugin_id: str):
    """Looks now whether a plugin installed from a repository has a newer version (the server also looks by itself)."""
    p = plugins.get(plugin_id)
    if not p or p["kind"] != "managed":
        raise HTTPException(404, tr("Plugin not found"))
    try:
        info = await asyncio.to_thread(pluginhost.check_update, plugin_id)
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())
    if info.get("available"):
        db.notify(db.SYSTEM, f"plugin_update:{plugin_id}", "plugin_update", {"id": plugin_id, "name": p["name"], "version": info.get("version", ""), "latest": info.get("latest", "")})
    else:
        db.resolve_notification(db.SYSTEM, f"plugin_update:{plugin_id}")
    return info


@admin.get("/updates")
def admin_updates():
    """Versions: the server (from GitHub, if UPDATE_REPO is set) and the plugins installed from a repository."""
    return sysmon.snapshot()


class ChannelIn(BaseModel):
    channel: Literal["stable", "beta"]


@admin.put("/updates/channel")
async def admin_updates_channel(body: ChannelIn):
    """Which versions are announced: stable (releases) or beta (also pre-releases). Looks again at once."""
    sysmon.set_channel(body.channel)
    log.info("Update channel set to %s", body.channel)
    await asyncio.to_thread(sysmon.check_server_update)
    return sysmon.snapshot()


@admin.post("/updates/check")
async def admin_updates_check():
    """Looks now for disk space problems and new versions (the server also does it by itself)."""
    return await asyncio.to_thread(sysmon.run_checks, True)


@admin.post("/plugins/{plugin_id}/refresh")
def admin_plugin_refresh(plugin_id: str):
    p = plugins.get(plugin_id)
    if not p:
        raise HTTPException(404, tr("Plugin not found"))
    if p["kind"] == "managed":
        pluginhost.restart(plugin_id)  # restart, which also re-reads the manifest
        return _plugin_view(plugins.get(plugin_id))
    return plugins.refresh(plugin_id)


@admin.post("/plugins/{plugin_id}/update")
async def admin_plugin_update(plugin_id: str):
    try:
        return _plugin_view(await asyncio.to_thread(pluginhost.update, plugin_id))
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


def _plugin_with_settings(plugin_id: str) -> dict:
    p = plugins.get(plugin_id)
    if not p:
        raise HTTPException(404, tr("Plugin not found"))
    return p


@admin.get("/plugins/{plugin_id}/settings")
def admin_plugin_settings(plugin_id: str):
    _plugin_with_settings(plugin_id)
    try:
        return plugins.settings_view(plugin_id)
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


class PluginSettingsIn(BaseModel):
    values: dict


@admin.put("/plugins/{plugin_id}/settings")
def admin_plugin_settings_save(plugin_id: str, body: PluginSettingsIn):
    """Saves the plugin's settings (checked against the schema from its manifest) and applies them in the running plugin."""
    _plugin_with_settings(plugin_id)
    try:
        return plugins.save_settings(plugin_id, body.values)
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


class PluginActionIn(BaseModel):
    values: dict = {}


@admin.post("/plugins/{plugin_id}/actions/{action_id}")
def admin_plugin_action(plugin_id: str, action_id: str, body: PluginActionIn):
    """Runs an action the plugin offers (e.g. adding a station to its source). Only admins."""
    _plugin_with_settings(plugin_id)
    try:
        return plugins.run_action(plugin_id, action_id, body.values)
    except plugins.PluginError as e:
        raise HTTPException(400, e.localized())


@admin.delete("/plugins/{plugin_id}")
def admin_plugin_remove(plugin_id: str):
    p = plugins.get(plugin_id)
    if not p:
        raise HTTPException(404, tr("Plugin not found"))
    if p["kind"] == "managed":
        pluginhost.remove(plugin_id)
    else:
        plugins.remove(plugin_id)
    return {"ok": True}


class EnrichIn(BaseModel):
    enabled: bool


@admin.get("/enrich")
def admin_enrich():
    return enrich.status()


@admin.post("/enrich")
def admin_enrich_set(body: EnrichIn):
    enrich.set_enabled(body.enabled)
    if body.enabled:
        enrich.maybe_run()
    return enrich.status()


@admin.post("/enrich/run")
def admin_enrich_run(retry: bool = False):
    enrich.maybe_run(retry)
    return enrich.status()


class LoggingIn(BaseModel):
    levels: dict[str, str]  # category id -> OFF/ERROR/WARNING/INFO/DEBUG, "" = back to environment/default


@admin.get("/logging")
def admin_logging():
    return logconf.status()


@admin.put("/logging")
def admin_logging_set(body: LoggingIn):
    try:
        return logconf.set_levels(body.levels)
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin.get("/logs")
def admin_logs(limit: int = Query(200, ge=1, le=500)):
    """The latest log messages kept in memory (shown in the admin area)."""
    return {"lines": logconf.recent(limit)}


@admin.get("/dashboard")
def admin_dashboard():
    """Key figures and health of the server for the admin area."""
    try:
        du = shutil.disk_usage(library.MUSIC_DIR if library.MUSIC_DIR.exists() else BASE)
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = {"total": 0, "free": 0}
    return {**db.dashboard_stats(), "disk": disk, "library": library.stats(), "version": VERSION_INFO["version"],
            "stream_stats": db.stream_stats_7d()}


@api.get("/status")
async def status(_: dict = Depends(admin_user)):
    return JSONResponse({**library.stats(), "version": VERSION_INFO["version"]})


# ---------- Home page ----------

async def _build_home(user_id: int) -> dict:
    recent = await asyncio.to_thread(db.recent_tracks, user_id, 20)
    candidates = await asyncio.to_thread(db.seed_candidates, user_id, 6)
    ids = [t["id"] for t in recent + candidates]
    alive = await asyncio.to_thread(library.existing, ids)
    alive |= {i for i in ids if plugins.is_enabled_id(i)}
    recent = [t for t in recent if t["id"] in alive]
    fresh = await asyncio.to_thread(library.recently_added, 12)
    if not recent:
        shelves = []
        if fresh:
            shelves.append({"kind": "new", "title": tr("Recently added"), "subtitle": "", "tracks": fresh})
            discover = await asyncio.to_thread(library.random_tracks, 12, {t["id"] for t in fresh})
            if discover:
                shelves.append({"kind": "discover", "title": tr("Discover"), "subtitle": tr("A random selection from your library"), "tracks": discover})
        return {"empty": not shelves, "shelves": shelves, "artists": []}
    blocked = await asyncio.to_thread(db.blocked_sets, user_id)
    seeds = [s for s in candidates if s["id"] in alive and _allowed([s], blocked)][:3]
    known = {t["id"] for t in recent}

    async def similar_to(seed: dict) -> list[dict]:
        if plugins.is_plugin_id(seed["id"]):  # plugin track: the source's radio (slow, hence in parallel)
            try:
                return await asyncio.to_thread(plugins.radio, seed["id"], 30)
            except plugins.PluginError:
                return []
        return await asyncio.to_thread(library.similar, seed["id"], 30)

    results = [_allowed(r, blocked) for r in await asyncio.gather(*(similar_to(s) for s in seeds))]
    lists = [[t for t in r if t["id"] not in known] for r in results]
    shelves = [{"kind": "recent", "title": tr("Recently played"), "subtitle": "", "tracks": recent[:12]}]
    mix = _interleave(lists, 30)
    if mix:
        shelves.append({"kind": "mix", "title": tr("Your Mix"), "subtitle": tr("Based on what you listened to recently"), "tracks": mix})
    for seed, r in zip(seeds, results):
        tracks = [t for t in r if t["id"] not in known][:12]
        if tracks:
            shelves.append({"kind": "because", "title": tr("Because you listened to “{title}”", title=seed["title"]), "subtitle": seed["artist"], "tracks": tracks})
    new = [t for t in fresh if t["id"] not in known][:12]
    if new:
        shelves.append({"kind": "new", "title": tr("Recently added"), "subtitle": "", "tracks": new})
    artists = await asyncio.to_thread(db.top_artists, user_id, 10)
    return {"empty": False, "shelves": shelves, "artists": artists}


# ---------- Automatic lists ("For you") ----------

@api.get("/auto")
async def auto_index(user: dict = Depends(current_user)):
    blocked = await asyncio.to_thread(db.blocked_sets, user["id"])
    return {"lists": await asyncio.to_thread(autolists.index, user["id"], lambda t: _allowed(t, blocked))}


@api.get("/auto/{lid:path}")
async def auto_get(lid: str, user: dict = Depends(current_user)):
    blocked = await asyncio.to_thread(db.blocked_sets, user["id"])
    res = await asyncio.to_thread(autolists.get, user["id"], lid, lambda t: _allowed(t, blocked))
    if not res:
        raise HTTPException(404, tr("List not found"))
    return res


# ---------- Playlists ----------

def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(400, tr("Name must not be empty"))
    return name


@api.get("/playlists")
def playlists(user: dict = Depends(current_user)):
    return {"playlists": db.list_playlists(user["id"])}


@api.post("/playlists")
def playlist_create(body: PlaylistIn, user: dict = Depends(current_user)):
    pid = db.create_playlist(user["id"], _clean_name(body.name))
    if body.tracks:
        db.add_tracks(user["id"], pid, [t.model_dump() for t in body.tracks])
    return {"id": pid}


@api.get("/playlists/{pid}")
def playlist_get(pid: int, user: dict = Depends(current_user)):
    p = db.get_playlist(user["id"], pid)
    if not p:
        raise HTTPException(404, tr("Playlist not found"))
    return p


@api.patch("/playlists/{pid}")
def playlist_rename(pid: int, body: PlaylistRename, user: dict = Depends(current_user)):
    if not db.rename_playlist(user["id"], pid, _clean_name(body.name)):
        raise HTTPException(404, tr("Playlist not found"))
    return {"ok": True}


@api.delete("/playlists/{pid}")
def playlist_delete(pid: int, user: dict = Depends(current_user)):
    if not db.delete_playlist(user["id"], pid):
        raise HTTPException(404, tr("Playlist not found"))
    return {"ok": True}


@api.post("/playlists/{pid}/tracks")
def playlist_add(pid: int, body: TracksIn, user: dict = Depends(current_user)):
    added = db.add_tracks(user["id"], pid, [t.model_dump() for t in body.tracks])
    if added is None:
        raise HTTPException(404, tr("Playlist not found"))
    return {"added": added}


@api.delete("/playlists/{pid}/tracks/{entry_id}")
def playlist_remove(pid: int, entry_id: int, user: dict = Depends(current_user)):
    if not db.remove_track(user["id"], pid, entry_id):
        raise HTTPException(404, tr("Entry not found"))
    return {"ok": True}


# ---------- "What's new" (shown once per user after an update) ----------

NEWS_FILE = BASE / "whatsnew.json"


def _vt(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _news_entries() -> list[dict]:
    try:
        data = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
        return [e for e in data if isinstance(e, dict) and e.get("version") and e.get("items")]
    except (OSError, ValueError):
        return []


def _news_item(item: dict) -> dict:
    """One news item in the request language: `title`/`text` are English, `title_de`/`text_de` (etc.) override them."""
    lang = i18n.current()
    return {"title": str(item.get(f"title_{lang}") or item.get("title", "")), "text": str(item.get(f"text_{lang}") or item.get("text", ""))}


@api.get("/news")
def news(user: dict = Depends(current_user)):
    """News since the last version seen. New accounts get nothing (they see the introduction)."""
    current = VERSION_INFO["version"]
    seen = db.get_news_seen(user["id"])
    if not seen:
        db.set_news_seen(user["id"], current)
        return {"version": current, "items": []}
    items: list[dict] = []
    for e in sorted(_news_entries(), key=lambda e: _vt(e["version"]), reverse=True):
        if _vt(seen) < _vt(e["version"]) <= _vt(current):
            items += [_news_item(i) for i in e["items"]]
    return {"version": current, "items": items}


@api.post("/news/seen")
def news_seen(user: dict = Depends(current_user)):
    db.set_news_seen(user["id"], VERSION_INFO["version"])
    return {"ok": True}


# ---------- Tempo analysis (DJ mode) ----------

_bpm_sem = threading.Semaphore(1)  # one analysis at a time, it briefly needs a core


def _bpm_sync(path: Path) -> dict | None:
    with _bpm_sem:
        return bpmlib.analyze(path)


@api.get("/bpm/{video_id}")
async def bpm_info(video_id: str, duration: str = Query("", max_length=12), user: dict = Depends(current_user)):
    """Tempo and first beat of a track for DJ mode. The analysis runs directly on the file from the library."""
    _check_id(video_id)
    if not bpmlib.available() or plugins.is_enabled_id(video_id):
        return {"ready": True, "usable": False}
    hit = db.get_bpm(video_id)
    if hit and (hit.get("ver", 1) >= bpmlib.ANALYSIS_VER or hit["conf"] < BPM_MIN_CONF or not library.file_path(video_id)):
        return {"ready": True, "usable": hit["conf"] >= BPM_MIN_CONF, **hit}  # old rows are caught up as soon as the file is available
    if _secs(duration) >= 600:  # long sets: no DJ transition
        return {"ready": True, "usable": False}
    path = library.file_path(video_id)
    if not path:
        raise HTTPException(404, tr("Track not found"))
    res = await asyncio.get_running_loop().run_in_executor(None, _bpm_sync, path)
    if not res:
        res = {"bpm": 0.0, "first_beat": 0.0, "conf": 0.0}
    res.setdefault("ver", bpmlib.ANALYSIS_VER)
    db.save_bpm(video_id, res)
    return {"ready": True, "usable": res["conf"] >= BPM_MIN_CONF, **res}


BPM_MIN_CONF = 0.35
ANALYZE_LIBRARY = os.environ.get("ANALYZE", "1") != "0"  # analyze new tracks in the background after a scan (tempo, key)
_analysis_thread: threading.Thread | None = None


def _analyze_missing() -> None:
    """Analyzes tracks without an entry one after another, so radio, mixes and DJ mode have data before a track is played."""
    if not bpmlib.available():
        return
    with db.db() as c:
        todo = [r["id"] for r in c.execute(
            "SELECT id FROM lib_tracks WHERE duration < 600 AND id NOT IN (SELECT video_id FROM bpm) ORDER BY added DESC")]
    for tid in todo:
        path = library.file_path(tid)
        if not path:
            continue
        res = _bpm_sync(path) or {"bpm": 0.0, "first_beat": 0.0, "conf": 0.0}
        res.setdefault("ver", bpmlib.ANALYSIS_VER)
        db.save_bpm(tid, res)
        time.sleep(0.5)  # do not keep the server busy permanently


def _analyze_missing_bg() -> None:
    global _analysis_thread
    if not ANALYZE_LIBRARY or (_analysis_thread and _analysis_thread.is_alive()):
        return
    _analysis_thread = threading.Thread(target=_analyze_missing, daemon=True, name="library-analysis")
    _analysis_thread.start()


library.AFTER_SCAN.append(_analyze_missing_bg)


# ---------- Lyrics (LRCLIB) ----------

LYRICS_ENABLED = os.environ.get("LYRICS", "1") != "0"
LYRICS_RETRY = 7 * 86400  # search again for lyrics not found after a week
_LRC_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")
_TITLE_JUNK = re.compile(r"\s*[\(\[][^)\]]*(official|video|audio|lyrics?|visuali[sz]er|remaster|hd|hq|4k|live)[^)\]]*[\)\]]", re.I)


def parse_lrc(text: str) -> list[list]:
    """LRC text -> [[seconds, line], ...] (sorted, empty lines kept as a pause)."""
    out: list[list] = []
    for raw in (text or "").splitlines():
        m = _LRC_LINE.match(raw.strip())
        if not m:
            continue
        frac = m.group(3) or "0"
        out.append([int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac)), m.group(4).strip()])
    out.sort(key=lambda x: x[0])
    return out


def _lrclib(path: str, params: dict):
    import urllib.parse
    import urllib.request
    url = "https://lrclib.net/api/" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME} (self-hosted music server)"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 (fixed https address)
        return json.loads(r.read().decode("utf-8"))


def _secs(s: str) -> int:
    parts = [int(p) for p in re.findall(r"\d+", s or "")][-3:]
    out = 0
    for p in parts:
        out = out * 60 + p
    return out


def _fetch_lyrics_sync(title: str, artist: str, duration: int) -> dict:
    title = _TITLE_JUNK.sub("", title).strip() or title
    artist = artist.split(",")[0].strip()
    item = None
    try:
        p = {"track_name": title, "artist_name": artist}
        if duration:
            p["duration"] = duration
        item = _lrclib("get", p)
    except Exception:  # 404 or network error: continue with the search below
        item = None
    if not item:
        try:
            res = _lrclib("search", {"track_name": title, "artist_name": artist})
        except Exception:
            return {"found": False, "error": True}
        res = [r for r in res if r.get("syncedLyrics") or r.get("plainLyrics")]
        if duration:
            res = [r for r in res if abs((r.get("duration") or 0) - duration) <= 5]
        item = res[0] if res else None
    if not item:
        return {"found": False}
    synced = parse_lrc(item.get("syncedLyrics") or "")
    plain = (item.get("plainLyrics") or "").strip()
    if not plain and synced:
        plain = "\n".join(x[1] for x in synced)
    if not synced and not plain:
        return {"found": False}
    return {"found": True, "synced": synced, "plain": plain}


@api.get("/lyrics/{video_id}")
async def lyrics(video_id: str, title: str = Query("", max_length=200), artist: str = Query("", max_length=200),
                 duration: str = Query("", max_length=12)):
    _check_id(video_id)
    if not LYRICS_ENABLED or not title:
        return {"found": False}
    hit = db.get_lyrics(video_id)
    if hit and (hit[0].get("found") or time.time() - hit[1] < LYRICS_RETRY) and not hit[0].get("error"):
        return hit[0]
    if hit and hit[0].get("error") and time.time() - hit[1] < 600:
        return hit[0]
    res = await asyncio.get_running_loop().run_in_executor(None, _fetch_lyrics_sync, title, artist, _secs(duration))
    db.save_lyrics(video_id, res)
    return res


# ---------- Listening history and home page ----------

@api.post("/me/played")
def played(track: Track, user: dict = Depends(current_user)):
    db.record_play(user["id"], track.model_dump())
    return {"ok": True}


def _interleave(lists: list[list[dict]], limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    i = 0
    while len(out) < limit and any(i < len(lst) for lst in lists):
        for lst in lists:
            if i < len(lst) and lst[i]["id"] not in seen:
                seen.add(lst[i]["id"])
                out.append(lst[i])
                if len(out) >= limit:
                    break
        i += 1
    return out


_home_cache: dict[int, tuple[int, float, dict, str]] = {}  # user_id -> (last play id, time, response, language)


def _allowed(tracks: list[dict], blocked: tuple[set[str], set[str]]) -> list[dict]:
    bt, ba = blocked
    return [t for t in tracks if t["id"] not in bt and not (t.get("artist_id") and t["artist_id"] in ba)]


@api.get("/home")
async def home(refresh: int = 0, user: dict = Depends(current_user)):
    uid = user["id"]
    latest = await asyncio.to_thread(db.latest_play_id, uid)
    hit = _home_cache.get(uid)
    if not refresh and hit and hit[0] == latest and time.time() - hit[1] < 900 and hit[3] == i18n.current():  # shelf titles are translated
        data = hit[2]
    else:
        data = await _build_home(uid)
        _home_cache[uid] = (latest, time.time(), data, i18n.current())
    if db.community_enabled():  # not cached: it changes without this user playing anything
        return {**data, "community": {"enabled": True, "following": len(db.following_ids(uid))}, "friends": await _friends(uid)}
    return data


# ---------- Search history, suggestions, likes ----------

class PickIn(BaseModel):
    type: Literal["track", "artist", "album"]
    id: str = Field(min_length=1, max_length=64)
    title: str = Field("", max_length=300)
    subtitle: str = Field("", max_length=300)
    thumb: str = Field("", max_length=500)
    track: Track | None = None

    @field_validator("thumb")
    @classmethod
    def _https_only(cls, v: str) -> str:
        return v if v.startswith(("https://", "/api/cover/")) else ""


@api.get("/searches")
def searches(user: dict = Depends(current_user)):
    return {"queries": db.list_searches(user["id"], 12), "picks": db.list_picks(user["id"], 8)}


@api.delete("/searches")
def searches_clear(user: dict = Depends(current_user)):
    db.clear_search_history(user["id"])
    return {"ok": True}


@api.delete("/searches/{sid}")
def search_delete(sid: int, user: dict = Depends(current_user)):
    if not db.delete_search(user["id"], sid):
        raise HTTPException(404, tr("Entry not found"))
    return {"ok": True}


@api.post("/me/search-picks")
def search_pick(body: PickIn, user: dict = Depends(current_user)):
    if body.type == "track" and body.track is None:
        raise HTTPException(422, tr("track missing"))
    db.record_pick(user["id"], body.model_dump())
    return {"ok": True}


@api.get("/likes")
def likes(user: dict = Depends(current_user)):
    return {"tracks": db.list_likes(user["id"])}


@api.post("/likes")
def like_add(track: Track, user: dict = Depends(current_user)):
    db.like_track(user["id"], track.model_dump())
    return {"ok": True}


@api.delete("/likes/{video_id}")
def like_remove(video_id: str, user: dict = Depends(current_user)):
    _check_id(video_id)
    db.unlike_track(user["id"], video_id)
    return {"ok": True}


# ---------- Remember position, "stop recommending" ----------

class PositionIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{2,32}:[A-Za-z0-9_.-]{1,64}$")
    pos: float = Field(ge=0, le=86400)
    dur: float = Field(ge=0, le=86400)


@api.put("/me/position")
def position_save(body: PositionIn, user: dict = Depends(current_user)):
    return {"saved": db.save_position(user["id"], body.id, body.pos, body.dur)}


@api.get("/me/position/{video_id}")
def position_get(video_id: str, user: dict = Depends(current_user)):
    _check_id(video_id)
    return db.get_position(user["id"], video_id) or {"pos": 0, "dur": 0}


class DislikeIn(BaseModel):
    kind: Literal["track", "artist"]
    id: str = Field(min_length=1, max_length=64)
    title: str = Field("", max_length=300)


@api.get("/dislikes")
def dislikes(user: dict = Depends(current_user)):
    return {"items": db.list_dislikes(user["id"])}


@api.post("/dislikes")
def dislike_add(body: DislikeIn, user: dict = Depends(current_user)):
    if body.kind == "track":
        _check_id(body.id)
    db.add_dislike(user["id"], body.kind, body.id, body.title)
    _home_cache.pop(user["id"], None)
    return {"ok": True}


@api.delete("/dislikes/{kind}/{ref}")
def dislike_remove(kind: Literal["track", "artist"], ref: str, user: dict = Depends(current_user)):
    if not db.remove_dislike(user["id"], kind, ref):
        raise HTTPException(404, tr("Entry not found"))
    _home_cache.pop(user["id"], None)
    return {"ok": True}


# ---------- Status and frontend ----------

@api.get("/version")
def version():
    return VERSION_INFO


# ---------- Security middleware ----------

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; "
       "media-src 'self' blob:; connect-src 'self'; manifest-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _netloc(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _cross_origin(headers: dict) -> bool:
    """State-changing requests must come from the own origin (Origin or Referer header, if the browser sends one)."""
    hosts = {headers.get("host", "").lower()}
    if TRUST_PROXY and headers.get("x-forwarded-host"):
        hosts.add(headers["x-forwarded-host"].split(",")[0].strip().lower())
    origin = headers.get("origin")
    if origin is not None:
        if origin.lower().rstrip("/") in ALLOWED_ORIGINS:
            return False
        return _netloc(origin) not in hosts  # "null" and foreign origins end up here
    referer = headers.get("referer")
    if referer:
        return _netloc(referer) not in hosts
    return False  # not a browser request (curl, tests): the CSRF token protects anyway


# upload endpoints that may exceed the normal request body limit (they enforce their own limit while streaming)
UPLOAD_LIMITS = {"/api/admin/restore": MAX_RESTORE_BYTES, "/api/admin/plugins/upload": pluginhost.MAX_ZIP_BYTES}


class SecurityMiddleware:
    """Pure ASGI middleware (streams audio without detours): language, origin check, body limit, security headers, no-store for the API."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        cookies = {}
        for part in headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            cookies[k] = v
        i18n.set_lang(i18n.resolve(cookies.get(i18n.LANG_COOKIE, ""), headers.get("accept-language", "")))
        path, method = scope["path"], scope["method"]
        https = _is_https(scope.get("scheme", "http"), headers)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    h.setdefault(k, v)
                if path.startswith("/api/") and not path.startswith(("/api/stream/", "/api/cover/")):
                    h["Cache-Control"] = "no-store"
                if https:
                    h.setdefault("Strict-Transport-Security", "max-age=15552000")
            await send(message)

        if method not in SAFE_METHODS:
            if _cross_origin(headers):
                await JSONResponse({"detail": tr("Request from foreign origin rejected")}, status_code=403)(scope, receive, send_wrapped)
                return
            try:
                too_big = int(headers.get("content-length", "0")) > UPLOAD_LIMITS.get(path, MAX_BODY)
            except ValueError:
                too_big = True
            if too_big:
                await JSONResponse({"detail": tr("Request too large")}, status_code=413)(scope, receive, send_wrapped)
                return
        if method not in SAFE_METHODS and path not in UPLOAD_LIMITS:
            # Content-Length can be left out (chunked transfer) or be a lie: count what actually arrives. Uploads count themselves while streaming.
            received = 0

            async def counting_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_BODY:
                        raise HTTPException(413, tr("Request too large"))  # an HTTPException passes through FastAPI's body parsing unchanged
                return message

            await self.app(scope, counting_receive, send_wrapped)
            return
        await self.app(scope, receive, send_wrapped)


app.add_middleware(SecurityMiddleware)


def _asset_version(root: Path) -> str:
    """A short hash of all frontend files: it changes with every change to them."""
    h = hashlib.sha1()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


ASSET_VERSION = _asset_version(BASE / "static")
_VERSIONED = re.compile(r'(\b(?:src|href)="(?:[\w.-]+\.(?:js|css)))"')


class NoCacheStatic(StaticFiles):
    """The page itself is always revalidated (otherwise Safari sticks to old versions). Its scripts and styles carry a hash of the frontend
    (app.js?v=...) and may be kept forever: a change gives them a new address. That way the app can show its page without the server."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200 and path in ("", ".", "index.html") and isinstance(resp, FileResponse):
            html = Path(resp.path).read_text(encoding="utf-8")
            return HTMLResponse(_VERSIONED.sub(rf'\1?v={ASSET_VERSION}"', html), headers={"Cache-Control": "no-cache"})
        immutable = resp.status_code == 200 and f"v={ASSET_VERSION}" in scope.get("query_string", b"").decode("latin-1")
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return resp


app.include_router(api)
app.include_router(admin)

# mount the frontend last, otherwise it swallows the API routes
app.mount("/", NoCacheStatic(directory=BASE / "static", html=True), name="static")
