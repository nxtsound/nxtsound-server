"""SQLite persistence: users (with password and admin), sessions, playlists, listening history, music library."""
import calendar
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from i18n import tr

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).parent / "data" / "server.db"))

_TRACK_FIELDS = """
  video_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  artist_id TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  album_id TEXT NOT NULL DEFAULT '',
  duration TEXT NOT NULL DEFAULT '',
  thumb TEXT NOT NULL DEFAULT ''
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lib_tracks(
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  album_artist TEXT NOT NULL DEFAULT '',
  artist_id TEXT NOT NULL DEFAULT '',
  album_id TEXT NOT NULL DEFAULT '',
  track_no INTEGER NOT NULL DEFAULT 0,
  disc_no INTEGER NOT NULL DEFAULT 0,
  year TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  duration REAL NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  mtime REAL NOT NULL DEFAULT 0,
  cover TEXT NOT NULL DEFAULT '',
  search TEXT NOT NULL DEFAULT '',
  added INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lib_artist ON lib_tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_lib_album ON lib_tracks(album_id, disc_no, track_no);
CREATE INDEX IF NOT EXISTS idx_lib_added ON lib_tracks(added DESC);
CREATE TABLE IF NOT EXISTS meta_artist(
  artist_id TEXT PRIMARY KEY,
  mbid TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  found INTEGER NOT NULL DEFAULT 0,
  fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_album(
  album_id TEXT PRIMARY KEY,
  mbid TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  found INTEGER NOT NULL DEFAULT 0,
  fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plugins(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  token TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  features TEXT NOT NULL DEFAULT '[]',
  search_kinds TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at INTEGER NOT NULL DEFAULT 0,
  last_ok INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'remote',
  dir TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  rev TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  {_TRACK_FIELDS},
  added_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pt_playlist ON playlist_tracks(playlist_id, id);
CREATE TABLE IF NOT EXISTS listening(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  seconds REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, day)
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stream_stats(
  day TEXT NOT NULL,
  mode TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, mode)
);
CREATE TABLE IF NOT EXISTS lyrics(
  video_id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  fetched_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bpm(
  video_id TEXT PRIMARY KEY,
  bpm REAL NOT NULL,
  first_beat REAL NOT NULL,
  conf REAL NOT NULL,
  fetched_at INTEGER NOT NULL,
  extra TEXT
);
CREATE TABLE IF NOT EXISTS plays(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  {_TRACK_FIELDS},
  played_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plays_user ON plays(user_id, id DESC);
CREATE TABLE IF NOT EXISTS follows(
  follower_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  followee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(follower_id, followee_id)
);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target INTEGER NOT NULL,
  key TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{{}}',
  created_at INTEGER NOT NULL,
  UNIQUE(target, key)
);
CREATE TABLE IF NOT EXISTS searches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  query TEXT NOT NULL COLLATE NOCASE,
  ts REAL NOT NULL,
  UNIQUE(user_id, query)
);
CREATE TABLE IF NOT EXISTS search_picks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  subtitle TEXT NOT NULL DEFAULT '',
  thumb TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '',
  ts REAL NOT NULL,
  UNIQUE(user_id, type, ref_id)
);
CREATE TABLE IF NOT EXISTS likes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  {_TRACK_FIELDS},
  liked_at INTEGER NOT NULL,
  UNIQUE(user_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_likes_user ON likes(user_id, id DESC);
CREATE TABLE IF NOT EXISTS positions(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  video_id TEXT NOT NULL,
  pos REAL NOT NULL,
  dur REAL NOT NULL,
  ts INTEGER NOT NULL,
  PRIMARY KEY(user_id, video_id)
);
CREATE TABLE IF NOT EXISTS dislikes(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('track','artist')),
  ref TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  ts INTEGER NOT NULL,
  PRIMARY KEY(user_id, kind, ref)
);
"""


def _now() -> int:
    return int(time.time())


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # add missing columns (databases from older versions)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        for name, ddl in (
            ("pw_hash", "TEXT NOT NULL DEFAULT ''"),
            ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
            ("onboarded", "INTEGER NOT NULL DEFAULT 0"),
            ("pw_temp", "INTEGER NOT NULL DEFAULT 0"),      # 1 = password was reset by the admin
            ("last_active", "INTEGER NOT NULL DEFAULT 0"),  # last use (accurate to about a minute)
            ("news_seen", "TEXT NOT NULL DEFAULT ''"),      # up to which version "What's new" was seen ('' = new account)
            ("lang", "TEXT NOT NULL DEFAULT ''"),           # UI language ('' = automatic, from the browser)
            ("notif_seen", "INTEGER NOT NULL DEFAULT 0"),   # highest notification ID the user has seen (the bell)
            ("share_listening", "INTEGER NOT NULL DEFAULT 0"),  # 1 = people who follow this user see what they listen to (community)
        ):
            if name not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
                if name == "news_seen":
                    c.execute("UPDATE users SET news_seen = '0.2.0'")  # existing users get the popup with the first update after this
        if "extra" not in {r["name"] for r in c.execute("PRAGMA table_info(bpm)")}:
            c.execute("ALTER TABLE bpm ADD COLUMN extra TEXT")  # key, intro/outro, first beat (analysis version 2)
        pcols = {r["name"] for r in c.execute("PRAGMA table_info(plugins)")}
        for name, ddl in (("kind", "TEXT NOT NULL DEFAULT 'remote'"), ("dir", "TEXT NOT NULL DEFAULT ''"), ("source", "TEXT NOT NULL DEFAULT ''"),
                          ("rev", "TEXT NOT NULL DEFAULT ''"), ("settings_schema", "TEXT NOT NULL DEFAULT '[]'"),
                          ("categories", "TEXT NOT NULL DEFAULT '[]'"), ("actions", "TEXT NOT NULL DEFAULT '[]'")):
            if name not in pcols:
                c.execute(f"ALTER TABLE plugins ADD COLUMN {name} {ddl}")  # plugins table from the first version (remote plugins only)
        scols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        if "csrf" not in scols:
            c.execute("ALTER TABLE sessions ADD COLUMN csrf TEXT NOT NULL DEFAULT ''")
            c.execute("DELETE FROM sessions")  # tokens used to be stored in plain text: invalidate all sessions, log in once more
        if "last_seen" not in scols:
            c.execute("ALTER TABLE sessions ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
            c.execute("UPDATE sessions SET last_seen = created_at")
        for name, ddl in (("device", "TEXT NOT NULL DEFAULT ''"), ("ua", "TEXT NOT NULL DEFAULT ''"), ("ip", "TEXT NOT NULL DEFAULT ''"),
                          ("last_ip", "TEXT NOT NULL DEFAULT ''"), ("active_at", "INTEGER NOT NULL DEFAULT 0")):
            if name not in scols:
                c.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")  # device list: name chosen by the user, browser, IP at login and at last use
                if name == "active_at":
                    c.execute("UPDATE sessions SET active_at = last_seen")
        # there is always an admin: for old databases the oldest user
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] and not c.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
            c.execute("UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)")


def _track(row) -> dict:
    return {
        "id": row["video_id"],
        "title": row["title"],
        "artist": row["artist"],
        "artist_id": row["artist_id"],
        "album": row["album"],
        "album_id": row["album_id"],
        "duration": row["duration"],
        "thumb": row["thumb"],
    }


def _track_values(t: dict) -> tuple:
    return (
        t["id"], t.get("title", ""), t.get("artist", ""), t.get("artist_id", ""),
        t.get("album", ""), t.get("album_id", ""), t.get("duration", ""), t.get("thumb", ""),
    )


_INSERT_COLS = "video_id, title, artist, artist_id, album, album_id, duration, thumb"


# ---------- Users and sessions ----------

SCRYPT_N = int(os.getenv("APP_SCRYPT_N", 2**16))  # 64 MiB, OWASP recommends N=2^16 and up (with r=8, p=2, or N=2^17, p=1)
SCRYPT_R, SCRYPT_P = 8, 2
_SCRYPT_MAXMEM = 512 * 1024 * 1024


def _scrypt(pw: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM)


def hash_password(pw: str) -> str:
    """Format: scrypt$N$r$p$salt$hash (parameters are part of the hash so they can be raised later)."""
    salt = secrets.token_bytes(16)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${_scrypt(pw, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P).hex()}"


def _parse_hash(stored: str):
    parts = stored.split("$")
    if parts[0] != "scrypt":
        raise ValueError("unbekanntes Format")
    if len(parts) == 3:  # old format without parameters
        return 2**14, 8, 1, parts[1], parts[2]
    if len(parts) == 6:
        return int(parts[1]), int(parts[2]), int(parts[3]), parts[4], parts[5]
    raise ValueError("unbekanntes Format")


def verify_password(pw: str, stored: str) -> bool:
    try:
        n, r, p, salt, h = _parse_hash(stored)
        return hmac.compare_digest(_scrypt(pw, bytes.fromhex(salt), n, r, p).hex(), h)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        n, r, p, _, _ = _parse_hash(stored)
    except (ValueError, TypeError):
        return True
    return (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


USER_COLS = "id, name, is_admin, onboarded, pw_hash, created_at, pw_temp, last_active, lang"


def _user(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "is_admin": bool(row["is_admin"]),
        "onboarded": bool(row["onboarded"]),
        "has_password": bool(row["pw_hash"]), "created_at": row["created_at"],
        "must_change_password": bool(row["pw_temp"]), "last_active": row["last_active"], "lang": row["lang"],
    }


def set_lang(user_id: int, lang: str) -> None:
    """Saves the user's UI language ('' = automatic)."""
    with db() as c:
        c.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))


def get_news_seen(user_id: int) -> str:
    with db() as c:
        r = c.execute("SELECT news_seen FROM users WHERE id = ?", (user_id,)).fetchone()
    return r["news_seen"] if r else ""


def set_news_seen(user_id: int, version: str) -> None:
    with db() as c:
        c.execute("UPDATE users SET news_seen = ? WHERE id = ?", (version, user_id))


def count_users() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(name: str, password: str, is_admin: bool | None = None) -> dict | None:
    """Creates a user. The very first one becomes admin. None if the name is taken."""
    with db() as c:
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if c.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
            return None
        admin = first if is_admin is None else (is_admin or first)
        cur = c.execute(
            "INSERT INTO users(name, created_at, pw_hash, is_admin) VALUES(?, ?, ?, ?)",
            (name, _now(), hash_password(password) if password else "", int(admin)),
        )
        row = c.execute(f"SELECT {USER_COLS} FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user(row)


def find_user(name: str) -> dict | None:
    with db() as c:
        row = c.execute(f"SELECT {USER_COLS} FROM users WHERE name = ?", (name,)).fetchone()
    return _user(row) if row else None


def check_login(name: str, password: str) -> dict | None:
    """Checks name and password. An admin without a password (old database) sets it with the first login."""
    with db() as c:
        row = c.execute(f"SELECT {USER_COLS} FROM users WHERE name = ?", (name,)).fetchone()
        if row is None:
            hash_password(password)  # same running time so names cannot be guessed
            return None
        if not row["pw_hash"]:
            if not row["is_admin"]:
                return None
            c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
            return _user(c.execute(f"SELECT {USER_COLS} FROM users WHERE id = ?", (row["id"],)).fetchone())
        if not verify_password(password, row["pw_hash"]):
            return None
        if needs_rehash(row["pw_hash"]):  # parameters were raised since storing: upgrade at login
            c.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
        return _user(row)


def claimable_admin() -> str | None:
    """Name of the admin who has no password yet (after updating an old database)."""
    with db() as c:
        row = c.execute("SELECT name FROM users WHERE is_admin = 1 AND pw_hash = '' LIMIT 1").fetchone()
    return row["name"] if row else None


def list_users() -> list[dict]:
    with db() as c:
        rows = c.execute(
            f"SELECT {USER_COLS} FROM users ORDER BY id"
        ).fetchall()
    return [_user(r) for r in rows]


def update_user(user_id: int, password: str | None = None, is_admin: bool | None = None, temp: bool = False) -> bool:
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            return False
        if password is not None:
            c.execute("UPDATE users SET pw_hash = ?, pw_temp = ? WHERE id = ?", (hash_password(password), int(temp), user_id))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # old logins become invalid
        if is_admin is not None:
            c.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))
    return True


def name_taken(name: str, except_id: int | None = None) -> bool:
    """Name already taken? Case does not matter; the entry with except_id (the user's own) is ignored."""
    with db() as c:
        row = c.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
    return row is not None and row["id"] != except_id


def rename_user(user_id: int, name: str) -> bool:
    """False if the name is taken."""
    try:
        with db() as c:
            c.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        return True
    except sqlite3.IntegrityError:
        return False


def admin_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]


def delete_user(user_id: int) -> None:
    """Deletes the user including their data."""
    with db() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        c.execute("DELETE FROM notifications WHERE target = ?", (user_id,))


def mark_onboarded(user_id: int) -> None:
    with db() as c:
        c.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (user_id,))


def _sid(token: str) -> str:
    """The DB holds only the hash of the cookie token: a DB leak yields no usable sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, ua: str = "", ip: str = "") -> tuple[str, str]:
    """Returns (cookie token, CSRF token). ua and ip are what the device list shows for this login."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with db() as c:
        c.execute("INSERT INTO sessions(token, user_id, created_at, last_seen, csrf, ua, ip, last_ip, active_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (_sid(token), user_id, _now(), _now(), csrf, ua, ip, ip, _now()))
        c.execute("UPDATE users SET last_active = ? WHERE id = ?", (_now(), user_id))
    return token, csrf


SESSION_TOUCH = 3600  # last_seen hoechstens stuendlich schreiben
ACTIVE_TOUCH = 60     # "last active" accurate to a minute


def user_for_token(token: str, ttl: int | None = None, max_age: int | None = None, ip: str = "") -> dict | None:
    """User for the session. With ttl (seconds) the session expires after ttl of inactivity (sliding):
    every use extends it. With max_age it ends that long after the login whatever happens (a stolen cookie cannot be kept alive for ever).
    user["_renew"] is True if the cookie should be set again. ip is where the request came from (shown in the device list)."""
    now = _now()
    with db() as c:
        row = c.execute(
            "SELECT u.id, u.name, u.is_admin, u.onboarded, u.pw_hash, u.created_at, u.pw_temp, u.last_active, u.lang, s.last_seen, s.csrf, s.created_at AS started, s.active_at, s.last_ip "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (_sid(token),),
        ).fetchone()
        if row is None:
            return None
        if (ttl is not None and now - row["last_seen"] > ttl) or (max_age and now - row["started"] > max_age):
            c.execute("DELETE FROM sessions WHERE token = ?", (_sid(token),))
            return None
        renew = now - row["last_seen"] > SESSION_TOUCH
        if renew:
            c.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (now, _sid(token)))
        if now - row["active_at"] > ACTIVE_TOUCH or (ip and ip != row["last_ip"]):
            c.execute("UPDATE sessions SET active_at = ?, last_ip = ? WHERE token = ?", (now, ip or row["last_ip"], _sid(token)))
        if now - row["last_active"] > ACTIVE_TOUCH:
            c.execute("UPDATE users SET last_active = ? WHERE id = ?", (now, row["id"]))
    return {**_user(row), "_renew": renew, "csrf": row["csrf"]}


def purge_sessions(ttl: int, max_age: int = 0) -> int:
    with db() as c:
        n = c.execute("DELETE FROM sessions WHERE last_seen < ?", (_now() - ttl,)).rowcount
        if max_age:
            n += c.execute("DELETE FROM sessions WHERE created_at < ?", (_now() - max_age,)).rowcount
        return n


def check_backup(path: Path) -> str | None:
    """Checks an uploaded backup. None = usable, otherwise the error message (in the request language)."""
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return tr("Not a valid database file.")
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return tr("The backup is damaged.")
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"users", "playlists", "plays", "likes", "settings"} <= tables:
            return tr("This is not a backup of this server.")
        if src.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] < 1:
            return tr("The backup contains no admin; afterwards nobody could get into the admin area.")
    except sqlite3.Error:
        return tr("Not a valid backup of this server.")
    finally:
        src.close()
    return None


def restore_from(source: Path) -> None:
    """Replaces the contents of the running database with the backup (SQLite backup API) and upgrades the schema.
    All sessions are ended."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
    try:
        dst = sqlite3.connect(DB_PATH, timeout=30)
        try:
            src.backup(dst)
            dst.execute("DELETE FROM sessions")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    init_db()


# ---------- Backup ----------

def backup_to(target: Path) -> None:
    """Consistent copy of the running database (SQLite backup API). Sessions are not included."""
    src = sqlite3.connect(DB_PATH, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute("DELETE FROM sessions")
            dst.commit()
            dst.execute("VACUUM")
        finally:
            dst.close()
    finally:
        src.close()


def delete_session(token: str) -> None:
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (_sid(token),))


SESSION_NAME_MAX = 40


def list_sessions(user_id: int, current_token: str) -> list[dict]:
    """The user's logins, the current one first, then the most recently used. "id" is only valid together with the user's ID."""
    mine = _sid(current_token) if current_token else ""
    with db() as c:
        rows = c.execute("SELECT rowid AS id, token, device, ua, ip, last_ip, created_at, active_at, last_seen FROM sessions WHERE user_id = ?", (user_id,)).fetchall()
    out = [{"id": r["id"], "name": r["device"], "ua": r["ua"], "ip": r["ip"], "last_ip": r["last_ip"], "created_at": r["created_at"],
            "last_active": r["active_at"] or r["last_seen"], "current": r["token"] == mine} for r in rows]
    return sorted(out, key=lambda x: (not x["current"], -x["last_active"]))


def rename_session(user_id: int, session_id: int, name: str) -> bool:
    """Sets the name the user gave the device (empty = back to the automatic name). False if the session is not theirs."""
    with db() as c:
        return c.execute("UPDATE sessions SET device = ? WHERE rowid = ? AND user_id = ?", (name.strip()[:SESSION_NAME_MAX], session_id, user_id)).rowcount > 0


def end_session(user_id: int, session_id: int, keep_token: str = "") -> str:
    """Logs one device out. Returns "ok", "missing" (not the user's) or "current" (the device asking: that is a log out)."""
    with db() as c:
        row = c.execute("SELECT token FROM sessions WHERE rowid = ? AND user_id = ?", (session_id, user_id)).fetchone()
        if row is None:
            return "missing"
        if keep_token and row["token"] == _sid(keep_token):
            return "current"
        c.execute("DELETE FROM sessions WHERE rowid = ?", (session_id,))
    return "ok"


def end_other_sessions(user_id: int, keep_token: str) -> int:
    """Logs every device out except the one asking. Returns how many were ended."""
    with db() as c:
        return c.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user_id, _sid(keep_token))).rowcount


# ---------- Playlists ----------

def list_playlists(user_id: int) -> list[dict]:
    with db() as c:
        rows = c.execute(
            """SELECT p.id, p.name,
                      (SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = p.id) AS count,
                      (SELECT thumb FROM playlist_tracks
                        WHERE playlist_id = p.id AND thumb != '' ORDER BY id LIMIT 1) AS thumb
                 FROM playlists p WHERE p.user_id = ?
                ORDER BY p.created_at DESC, p.id DESC""",
            (user_id,),
        ).fetchall()
    return [{"id": r["id"], "name": r["name"], "count": r["count"], "thumb": r["thumb"] or ""} for r in rows]


def create_playlist(user_id: int, name: str) -> int:
    with db() as c:
        cur = c.execute("INSERT INTO playlists(user_id, name, created_at) VALUES(?, ?, ?)", (user_id, name, _now()))
        return cur.lastrowid


def _owns(c, user_id: int, playlist_id: int) -> bool:
    return c.execute("SELECT 1 FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id)).fetchone() is not None


def get_playlist(user_id: int, playlist_id: int) -> dict | None:
    with db() as c:
        p = c.execute("SELECT id, name FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id)).fetchone()
        if p is None:
            return None
        rows = c.execute("SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY id", (playlist_id,)).fetchall()
    tracks = []
    for r in rows:
        t = _track(r)
        t["entry_id"] = r["id"]
        tracks.append(t)
    return {"id": p["id"], "name": p["name"], "tracks": tracks}


# ---------- Kapitel (langer Sets) ----------

def rename_playlist(user_id: int, playlist_id: int, name: str) -> bool:
    with db() as c:
        cur = c.execute("UPDATE playlists SET name = ? WHERE id = ? AND user_id = ?", (name, playlist_id, user_id))
        return cur.rowcount > 0


def delete_playlist(user_id: int, playlist_id: int) -> bool:
    with db() as c:
        cur = c.execute("DELETE FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id))
        return cur.rowcount > 0


def add_tracks(user_id: int, playlist_id: int, tracks: list[dict]) -> int | None:
    """Adds tracks (those already present are skipped). None if the playlist does not exist."""
    with db() as c:
        if not _owns(c, user_id, playlist_id):
            return None
        have = {r["video_id"] for r in c.execute("SELECT video_id FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))}
        added = 0
        for t in tracks:
            if t["id"] in have:
                continue
            have.add(t["id"])
            c.execute(
                f"INSERT INTO playlist_tracks(playlist_id, {_INSERT_COLS}, added_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (playlist_id, *_track_values(t), _now()),
            )
            added += 1
        return added


def remove_track(user_id: int, playlist_id: int, entry_id: int) -> bool:
    with db() as c:
        if not _owns(c, user_id, playlist_id):
            return False
        cur = c.execute("DELETE FROM playlist_tracks WHERE id = ? AND playlist_id = ?", (entry_id, playlist_id))
        return cur.rowcount > 0


# ---------- Hoerverlauf ----------

def record_play(user_id: int, t: dict) -> None:
    with db() as c:
        c.execute(
            f"INSERT INTO plays(user_id, {_INSERT_COLS}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, *_track_values(t), _now()),
        )


def latest_play_id(user_id: int) -> int:
    with db() as c:
        row = c.execute("SELECT MAX(id) AS m FROM plays WHERE user_id = ?", (user_id,)).fetchone()
    return row["m"] or 0


def recent_tracks(user_id: int, limit: int = 20) -> list[dict]:
    """Recently played, each song only once (the most recent entry counts)."""
    with db() as c:
        rows = c.execute(
            """SELECT p.* FROM plays p
                 JOIN (SELECT MAX(id) AS mid FROM plays WHERE user_id = ? GROUP BY video_id) m ON p.id = m.mid
                ORDER BY p.id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [_track(r) for r in rows]


def top_tracks(user_id: int, limit: int = 20) -> list[dict]:
    """The songs played most often (ties: the more recent one first), each once."""
    with db() as c:
        rows = c.execute(
            """SELECT p.* FROM plays p
                 JOIN (SELECT video_id, COUNT(*) AS n, MAX(id) AS mid FROM plays WHERE user_id = ? GROUP BY video_id ORDER BY n DESC, mid DESC LIMIT ?) m ON p.id = m.mid
                ORDER BY m.n DESC, m.mid DESC""",
            (user_id, limit),
        ).fetchall()
    return [_track(r) for r in rows]


def _last_plays(user_id: int, n: int = 300) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM plays WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
    return [_track(r) for r in rows]


def seed_candidates(user_id: int, n: int = 3) -> list[dict]:
    """Songs that recommendations are built from: played often and recently, preferably by different artists."""
    scores: dict[str, float] = {}
    first: dict[str, dict] = {}
    for i, t in enumerate(_last_plays(user_id)):  # i = 0 is the most recent play
        scores[t["id"]] = scores.get(t["id"], 0.0) + 1.0 / (1.0 + i / 15.0)
        first.setdefault(t["id"], t)
    for t in list_likes(user_id, 50):  # likes give a fixed bonus
        scores[t["id"]] = scores.get(t["id"], 0.0) + 1.5
        first.setdefault(t["id"], t)
    ranked = sorted(scores, key=lambda vid: -scores[vid])
    picked: list[dict] = []
    seen_artists: set[str] = set()
    for vid in ranked:
        t = first[vid]
        key = t["artist_id"] or t["artist"] or vid
        if key in seen_artists:
            continue
        seen_artists.add(key)
        picked.append(t)
        if len(picked) >= n:
            return picked
    for vid in ranked:  # fill up if there are too few different artists
        if len(picked) >= n:
            break
        if first[vid] not in picked:
            picked.append(first[vid])
    return picked


def known_tracks(user_id: int, ids: list[str]) -> dict[str, dict]:
    """Track data (as remembered when played) from history and likes; for plugin tracks that are not in the library."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        with db() as c:
            rows = list(c.execute(f"SELECT * FROM plays WHERE user_id = ? AND video_id IN ({marks}) ORDER BY id", [user_id, *chunk]))
            rows += list(c.execute(f"SELECT * FROM likes WHERE user_id = ? AND video_id IN ({marks}) ORDER BY id", [user_id, *chunk]))
        for r in rows:  # later entries overwrite earlier ones
            out[r["video_id"]] = _track(r)
    return out


def top_artists(user_id: int, limit: int = 10) -> list[dict]:
    counts: dict[str, int] = {}
    first: dict[str, dict] = {}
    for t in _last_plays(user_id):
        aid = t["artist_id"]
        if not aid:
            continue
        counts[aid] = counts.get(aid, 0) + 1
        first.setdefault(aid, t)
    ranked = sorted(counts, key=lambda a: -counts[a])[:limit]
    return [{"id": a, "name": first[a]["artist"].split(", ")[0], "thumb": first[a]["thumb"], "plays": counts[a]} for a in ranked]


# ---------- Community: follow each other, see what friends listen to ----------
# The admin turns the feature on or off. Nothing is shared by default: a user has to switch on "share what I listen to";
# only then do their followers see their recent plays.

def community_enabled() -> bool:
    return get_setting("community_enabled", "0") == "1"


def set_community_enabled(on: bool) -> None:
    set_setting("community_enabled", "1" if on else "0")


def get_share(user_id: int) -> bool:
    with db() as c:
        r = c.execute("SELECT share_listening FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(r and r["share_listening"])


def set_share(user_id: int, on: bool) -> None:
    with db() as c:
        c.execute("UPDATE users SET share_listening = ? WHERE id = ?", (int(on), user_id))


def follow(follower_id: int, followee_id: int) -> bool:
    """True if the user now follows (False if it was so already or the user cannot be followed: oneself, unknown)."""
    if follower_id == followee_id:
        return False
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id = ?", (followee_id,)).fetchone():
            return False
        return c.execute("INSERT OR IGNORE INTO follows(follower_id, followee_id, created_at) VALUES(?, ?, ?)", (follower_id, followee_id, _now())).rowcount > 0


def unfollow(follower_id: int, followee_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM follows WHERE follower_id = ? AND followee_id = ?", (follower_id, followee_id))


def following_ids(user_id: int) -> list[int]:
    with db() as c:
        return [r["followee_id"] for r in c.execute("SELECT followee_id FROM follows WHERE follower_id = ? ORDER BY created_at", (user_id,))]


def follower_count(user_id: int) -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM follows WHERE followee_id = ?", (user_id,)).fetchone()[0]


def people(user_id: int) -> list[dict]:
    """All other users with the relation to the user: following them, followed by them, and whether they share what they listen to."""
    with db() as c:
        mine = {r["followee_id"] for r in c.execute("SELECT followee_id FROM follows WHERE follower_id = ?", (user_id,))}
        theirs = {r["follower_id"] for r in c.execute("SELECT follower_id FROM follows WHERE followee_id = ?", (user_id,))}
        rows = c.execute("SELECT id, name, share_listening FROM users WHERE id != ? ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "shares": bool(r["share_listening"]), "following": r["id"] in mine, "follows_me": r["id"] in theirs} for r in rows]


NOW_SECONDS = 240  # a play this recent counts as "listening now"


def friends_feed(user_id: int, limit: int = 18, days: int = 7, per_friend: int = 3) -> list[dict]:
    """What the people the user follows listened to lately: only those who share, newest first, a few different songs per friend."""
    since = time.time() - days * 86400
    with db() as c:
        rows = c.execute(
            f"SELECT p.*, u.name AS friend FROM plays p JOIN follows f ON f.followee_id = p.user_id AND f.follower_id = ? "
            f"JOIN users u ON u.id = p.user_id WHERE u.share_listening = 1 AND p.played_at >= ? ORDER BY p.id DESC LIMIT 300", (user_id, since)).fetchall()
    out, seen = [], {}
    for r in rows:
        mine = seen.setdefault(r["user_id"], set())
        if r["video_id"] in mine or len(mine) >= per_friend:
            continue
        mine.add(r["video_id"])
        out.append({"user": {"id": r["user_id"], "name": r["friend"]}, "track": _track(r), "played_at": r["played_at"], "now": time.time() - r["played_at"] <= NOW_SECONDS})
        if len(out) >= limit:
            break
    return out


# ---------- Notifications (the bell) ----------
# target 0 = system messages (only admins see them: low disk space, updates), otherwise the ID of the user they are for.
# A message has a kind and data; the text is made when it is read, in the reader's language. `key` makes a message unique per target
# (the same warning is not stored twice); a changed message gets a new ID, so it counts as unread again.

SYSTEM = 0


def notify(target: int, key: str, kind: str, data: dict) -> bool:
    """Stores a message. False if the same message (same data) is already there."""
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    with db() as c:
        row = c.execute("SELECT id, data FROM notifications WHERE target = ? AND key = ?", (target, key)).fetchone()
        if row and row["data"] == blob:
            return False
        if row:
            c.execute("DELETE FROM notifications WHERE id = ?", (row["id"],))
        c.execute("INSERT INTO notifications(target, key, kind, data, created_at) VALUES(?, ?, ?, ?, ?)", (target, key, kind, blob, _now()))
    return True


def resolve_notification(target: int, key: str) -> None:
    """The reason is gone (disk space is fine again, the update was installed): the message goes away."""
    with db() as c:
        c.execute("DELETE FROM notifications WHERE target = ? AND key = ?", (target, key))


def resolve_notifications_like(target: int, prefix: str, keep: set[str]) -> None:
    """Removes the messages of a family (key starts with prefix) except the ones in `keep`."""
    with db() as c:
        for r in c.execute("SELECT key FROM notifications WHERE target = ? AND substr(key, 1, ?) = ?", (target, len(prefix), prefix)).fetchall():
            if r["key"] not in keep:
                c.execute("DELETE FROM notifications WHERE target = ? AND key = ?", (target, r["key"]))


def notifications_for(user_id: int, is_admin: bool, limit: int = 50) -> dict:
    """What the bell shows for a user: their own messages and, for admins, the system messages. Newest first."""
    targets = (user_id, SYSTEM) if is_admin else (user_id,)
    marks = ",".join("?" * len(targets))
    with db() as c:
        seen = c.execute("SELECT notif_seen FROM users WHERE id = ?", (user_id,)).fetchone()
        seen = seen["notif_seen"] if seen else 0
        rows = c.execute(f"SELECT id, target, key, kind, data, created_at FROM notifications WHERE target IN ({marks}) ORDER BY id DESC LIMIT ?", (*targets, limit)).fetchall()
        unread = c.execute(f"SELECT COUNT(*) FROM notifications WHERE target IN ({marks}) AND id > ?", (*targets, seen)).fetchone()[0]
    return {"unread": unread, "items": [{"id": r["id"], "system": r["target"] == SYSTEM, "key": r["key"], "kind": r["kind"], "data": json.loads(r["data"]),
                                         "created_at": r["created_at"], "unread": r["id"] > seen} for r in rows]}


def mark_notifications_seen(user_id: int) -> None:
    with db() as c:
        top = c.execute("SELECT COALESCE(MAX(id), 0) FROM notifications").fetchone()[0]
        c.execute("UPDATE users SET notif_seen = ? WHERE id = ?", (top, user_id))


def dismiss_notification(user_id: int, is_admin: bool, notification_id: int) -> bool:
    """Removes a message the user may see (their own; system messages only for admins)."""
    with db() as c:
        row = c.execute("SELECT target FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        if not row or not (row["target"] == user_id or (row["target"] == SYSTEM and is_admin)):
            return False
        c.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
    return True


# ---------- Recap (what a user listened to over a period) ----------

RECAP_DAYS = {"week": 7, "month": 30, "year": 365, "all": 0}


def _artist_key(t: dict) -> str:
    return t["artist_id"] or t["artist"].split(", ")[0].strip().lower()


def _streaks(days: set[str]) -> tuple[int, int]:
    """(current, longest) run of consecutive days with listening. The current run may end today or yesterday."""
    if not days:
        return 0, 0
    ordinals = sorted(calendar.timegm(time.strptime(d, "%Y-%m-%d")) // 86400 for d in days)  # UTC days, like the day strings: no daylight-saving surprises
    longest = run = 1
    for a, b in zip(ordinals, ordinals[1:]):
        run = run + 1 if b - a == 1 else 1
        longest = max(longest, run)
    today = calendar.timegm(time.strptime(_day(), "%Y-%m-%d")) // 86400
    current = 0
    if ordinals[-1] >= today - 1:  # still alive: today or yesterday was a listening day
        current = 1
        for a, b in zip(reversed(ordinals[:-1]), reversed(ordinals[1:])):
            if b - a != 1:
                break
            current += 1
    return current, longest


def recap(user_id: int, period: str = "month", tz: int = 0) -> dict:
    """Numbers for the user's recap page. tz: minutes the user's clock is ahead of UTC (for weekdays and hours)."""
    days = RECAP_DAYS[period]
    now = time.time()
    start = now - days * 86400 if days else 0
    with db() as c:
        rows = c.execute(f"SELECT {_INSERT_COLS}, played_at FROM plays WHERE user_id = ? AND played_at >= ? ORDER BY id", (user_id, start)).fetchall()
        listened = c.execute("SELECT day, seconds FROM listening WHERE user_id = ? AND seconds > 0", (user_id,)).fetchall()
        earlier = c.execute("SELECT artist_id, artist FROM plays WHERE user_id = ? AND played_at < ?", (user_id, start)).fetchall() if days else []
        prev = None
        if days:
            p_start = start - days * 86400
            prev = {"plays": c.execute("SELECT COUNT(*) FROM plays WHERE user_id = ? AND played_at >= ? AND played_at < ?", (user_id, p_start, start)).fetchone()[0],
                    "seconds": sum(r["seconds"] for r in listened if _day(p_start) <= r["day"] < _day(start))}
    tracks = [{**_track(r), "played_at": r["played_at"]} for r in rows]
    in_range = [r for r in listened if not days or r["day"] >= _day(start)]
    by_track: dict[str, dict] = {}
    by_artist: dict[str, dict] = {}
    sources: dict[str, int] = {}
    weekdays, hours = [0] * 7, [0] * 24
    for t in tracks:
        by_track.setdefault(t["id"], {"track": t, "plays": 0})["plays"] += 1
        if t["artist"]:
            a = by_artist.setdefault(_artist_key(t), {"id": t["artist_id"], "name": t["artist"].split(", ")[0], "thumb": t["thumb"], "plays": 0})
            a["plays"] += 1
            a["thumb"] = a["thumb"] or t["thumb"]
        src = t["id"].split(":")[0] if ":" in t["id"] else "local"
        sources[src] = sources.get(src, 0) + 1
        local = time.gmtime(t["played_at"] + tz * 60)
        weekdays[local.tm_wday] += 1
        hours[local.tm_hour] += 1
    known = {_artist_key({"artist_id": r["artist_id"], "artist": r["artist"]}) for r in earlier if r["artist"]}
    new_artists = [a for k, a in by_artist.items() if k not in known] if days else list(by_artist.values())
    best = max(in_range, key=lambda r: r["seconds"], default=None)
    current, longest = _streaks({r["day"] for r in listened})
    seconds = sum(r["seconds"] for r in in_range)
    ranked = lambda d, n: sorted(d.values(), key=lambda x: -x["plays"])[:n]  # noqa: E731
    return {
        "period": period, "days": days, "seconds": round(seconds), "plays": len(tracks), "songs": len(by_track), "artists": len(by_artist),
        "active_days": len(in_range), "average_seconds": round(seconds / len(in_range)) if in_range else 0,
        "best_day": {"day": best["day"], "seconds": round(best["seconds"])} if best else None,
        "streak": {"current": current, "longest": longest},
        "top_songs": [{**x["track"], "plays": x["plays"]} for x in ranked(by_track, 5)], "top_artists": ranked(by_artist, 5),
        "weekdays": weekdays, "hours": hours, "sources": [{"source": s, "plays": n} for s, n in sorted(sources.items(), key=lambda x: -x[1])],
        "new_artists": {"count": len(new_artists), "names": [a["name"] for a in sorted(new_artists, key=lambda a: -a["plays"])[:5]]},
        "previous": prev,
    }


# ---------- Suchverlauf ----------

def record_search(user_id: int, query: str) -> None:
    q = " ".join(query.split())[:200]
    if not q:
        return
    with db() as c:
        c.execute(
            "INSERT INTO searches(user_id, query, ts) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id, query) DO UPDATE SET ts = excluded.ts, query = excluded.query",
            (user_id, q, time.time()),
        )
        c.execute(
            "DELETE FROM searches WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM searches WHERE user_id = ? ORDER BY ts DESC LIMIT 50)",
            (user_id, user_id),
        )


def list_searches(user_id: int, limit: int = 12) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT id, query FROM searches WHERE user_id = ? ORDER BY ts DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [{"id": r["id"], "query": r["query"]} for r in rows]


def delete_search(user_id: int, search_id: int) -> bool:
    with db() as c:
        cur = c.execute("DELETE FROM searches WHERE id = ? AND user_id = ?", (search_id, user_id))
        return cur.rowcount > 0


def clear_search_history(user_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM searches WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM search_picks WHERE user_id = ?", (user_id,))


def record_pick(user_id: int, p: dict) -> None:
    """Remembers what the user opened from the search results (song, artist or album)."""
    payload = json.dumps(p["track"], ensure_ascii=False) if p.get("track") else ""
    with db() as c:
        c.execute(
            "INSERT INTO search_picks(user_id, type, ref_id, title, subtitle, thumb, payload, ts) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, type, ref_id) DO UPDATE SET title = excluded.title, subtitle = excluded.subtitle, "
            "thumb = excluded.thumb, payload = excluded.payload, ts = excluded.ts",
            (user_id, p["type"], p["id"], p.get("title", ""), p.get("subtitle", ""), p.get("thumb", ""), payload, time.time()),
        )
        c.execute(
            "DELETE FROM search_picks WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM search_picks WHERE user_id = ? ORDER BY ts DESC LIMIT 20)",
            (user_id, user_id),
        )


def list_picks(user_id: int, limit: int = 8) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT * FROM search_picks WHERE user_id = ? ORDER BY ts DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    out = []
    for r in rows:
        try:
            track = json.loads(r["payload"]) if r["payload"] else None
        except ValueError:
            track = None
        out.append({"type": r["type"], "id": r["ref_id"], "title": r["title"], "subtitle": r["subtitle"],
                    "thumb": r["thumb"], "track": track})
    return out


# ---------- Likes ----------

def like_track(user_id: int, t: dict) -> None:
    with db() as c:
        c.execute(
            f"INSERT OR IGNORE INTO likes(user_id, {_INSERT_COLS}, liked_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, *_track_values(t), _now()),
        )


def unlike_track(user_id: int, video_id: str) -> None:
    with db() as c:
        c.execute("DELETE FROM likes WHERE user_id = ? AND video_id = ?", (user_id, video_id))


def list_likes(user_id: int, limit: int = 500) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM likes WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [_track(r) for r in rows]


# ---------- Playback position (long tracks only, e.g. DJ sets) ----------

MIN_RESUME_DUR = 600   # seconds: shorter tracks are not remembered
MIN_RESUME_POS = 30    # resuming before this is not worth it
RESUME_TAIL = 30       # near the end counts as "finished"


def save_position(user_id: int, video_id: str, pos: float, dur: float) -> bool:
    """Remembers the position. Returns False if nothing was saved (too short, at the start/end)."""
    with db() as c:
        if dur < MIN_RESUME_DUR or pos < MIN_RESUME_POS or pos > dur:
            c.execute("DELETE FROM positions WHERE user_id = ? AND video_id = ? AND ? >= ?", (user_id, video_id, pos, dur - RESUME_TAIL))
            return False
        if pos >= dur - RESUME_TAIL:
            c.execute("DELETE FROM positions WHERE user_id = ? AND video_id = ?", (user_id, video_id))
            return False
        c.execute(
            "INSERT INTO positions(user_id, video_id, pos, dur, ts) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, video_id) DO UPDATE SET pos = excluded.pos, dur = excluded.dur, ts = excluded.ts",
            (user_id, video_id, pos, dur, _now()),
        )
        c.execute(
            "DELETE FROM positions WHERE user_id = ? AND video_id NOT IN "
            "(SELECT video_id FROM positions WHERE user_id = ? ORDER BY ts DESC LIMIT 50)",
            (user_id, user_id),
        )
    return True


def get_position(user_id: int, video_id: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT pos, dur FROM positions WHERE user_id = ? AND video_id = ?", (user_id, video_id)).fetchone()
    return {"pos": r["pos"], "dur": r["dur"]} if r else None


# ---------- "Stop recommending" ----------

def add_dislike(user_id: int, kind: str, ref: str, title: str = "") -> None:
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO dislikes(user_id, kind, ref, title, ts) VALUES(?, ?, ?, ?, ?)",
            (user_id, kind, ref, title, _now()),
        )


def remove_dislike(user_id: int, kind: str, ref: str) -> bool:
    with db() as c:
        return c.execute("DELETE FROM dislikes WHERE user_id = ? AND kind = ? AND ref = ?", (user_id, kind, ref)).rowcount > 0


def list_dislikes(user_id: int) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT kind, ref, title FROM dislikes WHERE user_id = ? ORDER BY ts DESC", (user_id,)).fetchall()
    return [{"kind": r["kind"], "id": r["ref"], "title": r["title"]} for r in rows]


def blocked_sets(user_id: int) -> tuple[set[str], set[str]]:
    """(track IDs, artist IDs) this user does not want recommended any more."""
    tracks: set[str] = set()
    artists: set[str] = set()
    for d in list_dislikes(user_id):
        (tracks if d["kind"] == "track" else artists).add(d["id"])
    return tracks, artists


# ---------- Listening time and key figures (admin dashboard) ----------

def _day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def add_listening(user_id: int, seconds: float) -> None:
    """Adds real listening time (seconds) to the user's current day."""
    if seconds <= 0:
        return
    with db() as c:
        c.execute(
            "INSERT INTO listening(user_id, day, seconds) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET seconds = seconds + excluded.seconds",
            (user_id, _day(), seconds),
        )


def dashboard_stats() -> dict:
    week = _day(time.time() - 6 * 86400)
    with db() as c:
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0  # noqa: E731
        return {
            "plays_total": one("SELECT COUNT(*) FROM plays"),
            "plays_7d": one("SELECT COUNT(*) FROM plays WHERE played_at >= ?", time.time() - 7 * 86400),
            "seconds_total": one("SELECT SUM(seconds) FROM listening"),
            "seconds_7d": one("SELECT SUM(seconds) FROM listening WHERE day >= ?", week),
            "users": one("SELECT COUNT(*) FROM users"),
            "active_7d": one("SELECT COUNT(*) FROM users WHERE last_active >= ?", time.time() - 7 * 86400),
            "tracks": one("SELECT COUNT(*) FROM lib_tracks"),
            "library_bytes": one("SELECT SUM(size) FROM lib_tracks"),
            "library_seconds": one("SELECT SUM(duration) FROM lib_tracks"),
        }


# ---------- Songtexte (Zwischenspeicher) ----------

def get_lyrics(video_id: str) -> tuple[dict, int] | None:
    with db() as c:
        r = c.execute("SELECT data, fetched_at FROM lyrics WHERE video_id = ?", (video_id,)).fetchone()
    return (json.loads(r["data"]), r["fetched_at"]) if r else None


def save_lyrics(video_id: str, data: dict) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO lyrics(video_id, data, fetched_at) VALUES(?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
            (video_id, json.dumps(data, ensure_ascii=False), _now()),
        )


# ---------- Tempo analysis (DJ mode) ----------

def get_bpm(video_id: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT bpm, first_beat, conf, extra FROM bpm WHERE video_id = ?", (video_id,)).fetchone()
    if not r:
        return None
    out = {"bpm": r["bpm"], "first_beat": r["first_beat"], "conf": r["conf"]}
    if r["extra"]:
        try:
            out.update(json.loads(r["extra"]))
        except ValueError:
            pass
    return out


def get_bpm_many(ids: list[str], min_conf: float = 0.35) -> dict[str, dict]:
    """Tempo and key of several tracks at once; only confident analyses (id -> {"bpm", "key"})."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        with db() as c:
            rows = c.execute(f"SELECT video_id, bpm, extra FROM bpm WHERE conf >= ? AND bpm > 0 AND video_id IN ({','.join('?' * len(chunk))})",
                             [min_conf, *chunk]).fetchall()
        for r in rows:
            key = ""
            if r["extra"]:
                try:
                    e = json.loads(r["extra"])
                    key = e.get("key", "") if e.get("key_conf", 0) >= 0.2 else ""
                except ValueError:
                    pass
            out[r["video_id"]] = {"bpm": r["bpm"], "key": key}
    return out


def save_bpm(video_id: str, res: dict) -> None:
    extra = {k: v for k, v in res.items() if k not in ("bpm", "first_beat", "conf")}
    with db() as c:
        c.execute(
            "INSERT INTO bpm(video_id, bpm, first_beat, conf, fetched_at, extra) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET bpm = excluded.bpm, first_beat = excluded.first_beat, conf = excluded.conf, "
            "fetched_at = excluded.fetched_at, extra = excluded.extra",
            (video_id, res["bpm"], res["first_beat"], res["conf"], _now(), json.dumps(extra) if extra else None),
        )


# ---------- Settings and start-up measurement ----------

def get_setting(key: str, default: str = "") -> str:
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def add_stream_stat(mode: str, ms: int) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO stream_stats(day, mode, n, ms) VALUES(?, ?, 1, ?) "
            "ON CONFLICT(day, mode) DO UPDATE SET n = n + 1, ms = ms + excluded.ms",
            (_day(time.time()), mode, max(0, int(ms))),
        )


def stream_stats_7d() -> dict:
    week = _day(time.time() - 6 * 86400)
    with db() as c:
        rows = c.execute("SELECT mode, SUM(n) AS n, SUM(ms) AS ms FROM stream_stats WHERE day >= ? GROUP BY mode", (week,)).fetchall()
    return {r["mode"]: {"n": r["n"], "avg_ms": round(r["ms"] / r["n"]) if r["n"] else 0} for r in rows}
