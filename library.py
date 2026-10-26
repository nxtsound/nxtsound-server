"""Local music library: folder scan with tags and covers, search, artist/album pages, similar tracks."""
import base64
import hashlib
import logging
import os
import random
import re
import threading
import time
import uuid
from pathlib import Path

import mutagen
from mutagen.flac import Picture

import db
import enrich
from i18n import tr, trn

log = logging.getLogger("library")

BASE = Path(__file__).parent
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", BASE / "music"))
COVER_DIR = Path(os.getenv("COVER_DIR", BASE / "data" / "covers"))
# Second, writable library folder: plugins put songs they fetched for keeping here (the music folder is usually mounted read-only).
# Its tracks are stored with a path prefix so both folders can hold files with the same relative path.
IMPORT_DIR = Path(os.getenv("IMPORT_DIR", db.DB_PATH.parent / "imported"))
IMPORT_PREFIX = "+imported/"
_plugin_dirs: set[str] = set()  # IDs of the registered plugins; a plugin's own folder (<root>/<plugin-id>/...) is not part of artist/album guessing
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aif", ".aiff"}
COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")
NS = uuid.UUID("6f1a3b0e-6f43-4c1a-9a53-2f5c0e7a1d11")  # fixed namespace: the same file always gets the same ID
UNKNOWN_ARTIST = "Unknown artist"  # stored in the database as is; shown translated (see `shown`)

_state = {"running": False, "started": 0.0, "finished": 0.0, "added": 0, "updated": 0, "removed": 0, "errors": 0, "seen": 0}
_lock = threading.Lock()


# ---------- IDs and helpers ----------

def track_id_for(rel: str) -> str:
    return "local:" + str(uuid.uuid5(NS, rel))


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _gid(prefix: str, text: str) -> str:
    return prefix + hashlib.sha1(_norm(text).encode()).hexdigest()[:14]


def fmt_duration(sec: float) -> str:
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _int(text: str) -> int:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else 0


# ---------- Tags and covers ----------

def _first(tags, key: str) -> str:
    try:
        v = tags.get(key)
    except Exception:
        return ""
    if not v:
        return ""
    return str(v[0] if isinstance(v, (list, tuple)) else v).strip()


_EASY_KEYS = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist", "genre": "genre",
              "date": "date", "track": "tracknumber", "disc": "discnumber"}
_ID3_KEYS = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "genre": "TCON",
             "date": "TDRC", "track": "TRCK", "disc": "TPOS"}


def _embedded_cover(path: Path) -> tuple[bytes, str] | None:
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    try:
        if getattr(f, "pictures", None):  # FLAC
            p = f.pictures[0]
            return p.data, p.mime
        tags = f.tags
        if tags is None:
            return None
        if hasattr(tags, "getall"):  # ID3 (MP3, WAV, AIFF)
            apics = tags.getall("APIC")
            if apics:
                return apics[0].data, apics[0].mime
        covr = tags.get("covr") if hasattr(tags, "get") else None  # MP4
        if covr:
            c = covr[0]
            return bytes(c), "image/png" if getattr(c, "imageformat", 0) == 14 else "image/jpeg"
        mbp = tags.get("metadata_block_picture") if hasattr(tags, "get") else None  # Ogg/Opus
        if mbp:
            pic = Picture(base64.b64decode(mbp[0]))
            return pic.data, pic.mime
    except Exception:
        return None
    return None


def _sidecar_cover(path: Path) -> Path | None:
    """A picture named like the song (`Title.m4a` next to `Title.jpg`)."""
    for ext in COVER_EXT:
        p = path.with_suffix(ext)
        if p.is_file():
            return p
    return None


def _folder_cover(folder: Path) -> Path | None:
    try:
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for name in COVER_NAMES:
        for ext in COVER_EXT:
            if name + ext in files:
                return files[name + ext]
    return None


def _store_cover(data: bytes, mime: str) -> str:
    ext = ".png" if "png" in mime else ".webp" if "webp" in mime else ".jpg"
    name = hashlib.sha1(data).hexdigest()[:20] + ext
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    target = COVER_DIR / name
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return name


# ---------- Detection from path and file name ----------

_TRACK_PREFIX = re.compile(r"^(?:(\d{1,2})[-.](\d{2,3})|(\d{1,3}))(?:\s*[-.)]\s*|\s+)(?=\S)")
_SPLIT = re.compile(r"\s+[-–—]\s+")
_DISC_DIR = re.compile(r"^(?:cd|disc|disk)\s*0*(\d{1,2})$", re.I)
_YEAR_DIR = re.compile(r"^(\d{4})\s*[-–]\s+(.+)$|^(.+?)\s*[(\[](\d{4})[)\]]$")


def guess_from_path(rel: str) -> dict:
    """Derives title, artist, album, number and year from folders and file name (tags take precedence).

    Folders: `Artist/Album/Title.ext` (optional `CD1` subfolder, `2020 - Album`, `Album (2020)`).
    File name: `01 - Title`, `Artist - Title`, `Artist - Album - 01 - Title`, `Artist-Title`, `Artist_-_Title`.
    """
    p = Path(rel)
    stem = p.stem.strip()
    if " " not in stem and "_" in stem:
        stem = stem.replace("_", " ").strip()
    dirs = list(p.parent.parts) if str(p.parent) != "." else []
    out = {"title": "", "artist": "", "album": "", "album_artist": "", "track_no": 0, "disc_no": 0, "year": ""}

    m = _TRACK_PREFIX.match(stem)
    # "99 Luftballons": without an explicit separator only a leading zero counts as a track number
    if m and (m.group(1) or re.search(r"[-.)]", m.group(0)) or m.group(3).startswith("0")):
        if m.group(1):
            out["disc_no"], out["track_no"] = int(m.group(1)), int(m.group(2))
        else:
            out["track_no"] = int(m.group(3))
        stem = stem[m.end():].strip()

    parts = [x.strip() for x in _SPLIT.split(stem) if x.strip()]
    if len(parts) == 1 and stem.count("-") == 1 and not stem.startswith("-") and not stem.endswith("-"):
        parts = [x.strip() for x in stem.split("-") if x.strip()]  # "Artist-Title" without spaces
    nums = [x for x in parts[:-1] if x.isdigit()]  # "Artist - 03 - Title"
    if nums:
        out["track_no"] = out["track_no"] or int(nums[0])
        parts = [x for x in parts if x not in nums] or parts[-1:]
    fn_artist = fn_album = ""
    if len(parts) >= 3:
        fn_artist, fn_album, out["title"] = parts[0], parts[1], " - ".join(parts[2:])
    elif len(parts) == 2:
        fn_artist, out["title"] = parts
    elif parts:
        out["title"] = parts[0]

    if dirs and (d := _DISC_DIR.match(dirs[-1])):
        out["disc_no"] = out["disc_no"] or int(d.group(1))
        dirs = dirs[:-1]
    folder_album = dirs[-1] if dirs else ""
    if (y := _YEAR_DIR.match(folder_album)):
        out["year"], folder_album = (y.group(1), y.group(2)) if y.group(1) else (y.group(4), y.group(3))
    folder_artist = dirs[-2] if len(dirs) >= 2 else ""
    if not folder_artist and fn_artist and _norm(folder_album) == _norm(fn_artist):
        folder_album = ""  # "Artist/Artist - Title": the folder is the artist, not an album

    out["album"] = folder_album or fn_album
    out["artist"] = fn_artist or folder_artist
    out["album_artist"] = folder_artist or fn_artist
    return out


def import_root() -> Path:
    """Where plugins keep songs: a folder per plugin directly in the music folder if that is writable (mounted read-write),
    otherwise in the import folder."""
    return MUSIC_DIR if MUSIC_DIR.is_dir() and os.access(MUSIC_DIR, os.W_OK) else IMPORT_DIR


def _plugin_ids() -> set[str]:
    try:
        with db.db() as c:
            return {r["id"] for r in c.execute("SELECT id FROM plugins")}
    except Exception:  # noqa: BLE001 (before the database exists)
        return set()


def _without_plugin_folder(rel: str) -> str:
    parts = rel.split("/")
    return "/".join(parts[1:]) if len(parts) > 1 and parts[0] in _plugin_dirs else rel


def roots() -> list[tuple[str, Path]]:
    """Library folders as (path prefix in the database, folder)."""
    return [("", MUSIC_DIR), (IMPORT_PREFIX, IMPORT_DIR)]


def read_file(path: Path, rel: str) -> dict:
    """Reads tags, duration and cover. Missing tags are derived from file name and folder (the prefix of the import folder does not count)."""
    title = artist = album = album_artist = genre = year = ""
    track_no = disc_no = 0
    duration = 0.0
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is not None:
        duration = float(getattr(f.info, "length", 0) or 0)
        tags = f.tags or {}
        keys = _ID3_KEYS if hasattr(tags, "getall") else _EASY_KEYS  # WAV/AIFF return raw ID3 frames
        title, artist, album = (_first(tags, keys[k]) for k in ("title", "artist", "album"))
        album_artist, genre = _first(tags, keys["albumartist"]), _first(tags, keys["genre"])
        year = _first(tags, keys["date"])[:4]
        track_no, disc_no = _int(_first(tags, keys["track"])), _int(_first(tags, keys["disc"]))
    g = guess_from_path(_without_plugin_folder(rel.removeprefix(IMPORT_PREFIX)))
    title = title or g["title"] or path.stem
    album = album or g["album"]
    if artist:
        album_artist = album_artist or artist
    else:
        artist = album_artist or g["artist"] or UNKNOWN_ARTIST
        album_artist = album_artist or g["album_artist"] or artist
    track_no, disc_no, year = track_no or g["track_no"], disc_no or g["disc_no"], year or g["year"]
    cover = ""
    emb = _embedded_cover(path)
    if emb and emb[0]:
        cover = _store_cover(*emb)
    else:
        fc = _sidecar_cover(path) or _folder_cover(path.parent)
        if fc:
            try:
                cover = _store_cover(fc.read_bytes(), "image/png" if fc.suffix == ".png" else "image/jpeg")
            except OSError:
                pass
    st = path.stat()
    return {
        "id": track_id_for(rel), "path": rel, "title": title, "artist": artist, "album": album,
        "album_artist": album_artist, "artist_id": _gid("a", album_artist),
        "album_id": _gid("b", album_artist + "|" + album) if album else "",
        "track_no": track_no, "disc_no": disc_no, "year": year, "genre": genre, "duration": duration,
        "size": st.st_size, "mtime": st.st_mtime, "cover": cover,
        "search": _norm(f"{title} {'' if artist == UNKNOWN_ARTIST else artist} {album} {'' if album_artist == UNKNOWN_ARTIST else album_artist}"),
    }


# ---------- Scan ----------

def start_scan(full: bool = False) -> dict:
    """Starts the scan in the background (never twice)."""
    if _state["running"] or _lock.locked():
        return scan_state()
    _state["running"] = True
    threading.Thread(target=_scan_then_enrich, args=(full,), daemon=True, name="library-scan").start()
    return scan_state()


AFTER_SCAN: list = []  # callbacks after a background scan (e.g. DJ analysis), registered by main.py


def _scan_then_enrich(full: bool) -> None:
    scan(full)
    enrich.maybe_run()
    for hook in AFTER_SCAN:
        try:
            hook()
        except Exception:  # a callback must not end the scan thread
            log.exception("After-scan callback failed")


def existing(ids: list[str]) -> set[str]:
    """Which of the track IDs (still) exist in the library?"""
    if not ids:
        return set()
    with db.db() as c:
        marks = ",".join("?" * len(ids))
        return {r["id"] for r in c.execute(f"SELECT id FROM lib_tracks WHERE id IN ({marks})", ids)}


IMPORT_POLL_SECONDS = 45


def import_signature() -> tuple[int, float]:
    """(number of files, newest change) in the import folder: a cheap way to notice that a plugin saved something."""
    count, newest = 0, 0.0
    for base in [IMPORT_DIR] + [MUSIC_DIR / pid for pid in sorted(_plugin_ids())]:  # plugins write to the import folder or to their folder in the music folder
        for root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if name.startswith("."):
                    continue
                try:
                    newest = max(newest, (Path(root) / name).stat().st_mtime)
                    count += 1
                except OSError:
                    pass
    return count, newest


def watch_imports(stop: threading.Event) -> None:
    """Rescans (changes only) when the import folder has changed, so songs a plugin kept show up in the library by themselves."""
    seen = import_signature()
    while not stop.wait(IMPORT_POLL_SECONDS):
        now = import_signature()
        if now != seen:
            seen = now
            log.info("New files in the import folder, scanning")
            start_scan()


def scan_state() -> dict:
    return dict(_state)


def scan(full: bool = False) -> dict:
    """Syncs the database with the music folder (read new/changed files, remove missing ones)."""
    if not _lock.acquire(blocking=False):
        return scan_state()
    try:
        _state.update(running=True, started=time.time(), added=0, updated=0, removed=0, errors=0, seen=0)
        _plugin_dirs.clear()
        _plugin_dirs.update(_plugin_ids())
        log.info("Scan started (%s)", "full" if full else "changes only")
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        with db.db() as c:
            known = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT path, mtime, size FROM lib_tracks")}
        found: set[str] = set()
        for prefix, base in roots():
            for root, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    p = Path(root) / name
                    if name.startswith(".") or p.suffix.lower() not in AUDIO_EXT:
                        continue
                    rel = prefix + p.relative_to(base).as_posix()
                    found.add(rel)
                    _state["seen"] += 1
                    try:
                        st = p.stat()
                        if not full and known.get(rel) == (st.st_mtime, st.st_size):
                            continue
                        info = read_file(p, rel)
                        log.debug("Read %s", rel)
                        if not info["year"] or not info["cover"]:  # previously looked-up details, if the file has none itself
                            year, cover = enrich.cached_album(info["album_id"])
                            info["year"], info["cover"] = info["year"] or year, info["cover"] or cover
                    except Exception as e:
                        _state["errors"] += 1
                        log.warning("Scan %s: %s", rel, e)
                        continue
                    with db.db() as c:
                        exists = c.execute("SELECT added FROM lib_tracks WHERE path = ?", (rel,)).fetchone()
                        info["added"] = exists["added"] if exists else int(time.time())
                        cols = ", ".join(info)
                        marks = ", ".join(f":{k}" for k in info)
                        c.execute(f"INSERT OR REPLACE INTO lib_tracks({cols}) VALUES({marks})", info)
                    _state["updated" if exists else "added"] += 1
        gone = [p for p in known if p not in found]
        if gone:
            with db.db() as c:
                c.executemany("DELETE FROM lib_tracks WHERE path = ?", [(p,) for p in gone])
            _state["removed"] = len(gone)
        log.info("Scan finished: %d seen, %d added, %d updated, %d removed, %d errors", _state["seen"], _state["added"], _state["updated"], _state["removed"], _state["errors"])
        return scan_state()
    finally:
        _state.update(running=False, finished=time.time())
        _lock.release()


# ---------- Queries ----------

def shown(name: str) -> str:
    """Artist name for display: the stored placeholder for unknown artists is translated."""
    return tr(name) if name == UNKNOWN_ARTIST else name


def _track(r) -> dict:
    return {
        "id": r["id"], "title": r["title"], "artist": shown(r["artist"]), "artist_id": r["artist_id"],
        "album": r["album"], "album_id": r["album_id"], "duration": fmt_duration(r["duration"]),
        "thumb": f"/api/cover/{r['id']}" if r["cover"] else "",
    }


# ---------- The same song twice (library and plugin) ----------

_FEAT = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\b[^\)\]]*[\)\]]", re.I)
_twins: tuple | None = None


def _seconds(text) -> float:
    """"2:47" -> 167.0 (0 if unknown)."""
    try:
        parts = [int(p) for p in str(text).split(":")]
    except ValueError:
        return 0.0
    return float(sum(p * 60 ** i for i, p in enumerate(reversed(parts)))) if parts and len(parts) <= 3 else 0.0


def _title_key(title: str) -> str:
    return re.sub(r"[^\w ]+", "", _norm(_FEAT.sub("", title or "")))


def _artist_keys(artist: str) -> set[str]:
    parts = re.split(r",|&|;|/| x | feat\.? | ft\.? | with ", _norm(artist or ""))
    return {re.sub(r"[^\w ]+", "", p).strip() for p in parts if p.strip()}


def _twin_index() -> dict:
    """Title (without "feat.") -> the library songs of that title; made again when the library changes."""
    global _twins
    with db.db() as c:
        stamp = tuple(c.execute("SELECT COUNT(*), COALESCE(MAX(mtime), 0) FROM lib_tracks").fetchone())
        if _twins and _twins[0] == stamp:
            return _twins[1]
        index: dict[str, list] = {}
        for r in c.execute("SELECT id, title, artist, duration FROM lib_tracks"):
            index.setdefault(_title_key(r["title"]), []).append((r["id"], _artist_keys(r["artist"]), r["duration"] or 0.0))
    _twins = (stamp, index)
    return index


def twin_of(t: dict) -> str | None:
    """The ID of the library song that is the same as the (plugin) song `t`: the same title (feat. does not count), one artist in common,
    and if both durations are known, within 4 s of each other."""
    if not t or str(t.get("id", "")).startswith("local:") or not t.get("title"):
        return None
    mine, dur = _artist_keys(t.get("artist", "")), _seconds(t.get("duration"))
    for tid, theirs, d in _twin_index().get(_title_key(t["title"]), []):
        if (mine & theirs or (not mine and not theirs)) and (not dur or not d or abs(dur - d) <= 4):
            return tid
    return None


def prefer_library(tracks: list[dict]) -> list[dict]:
    """A plugin song that is in the library as well is replaced by the library's: it plays from the files (and can be mixed by the DJ).
    Every song appears once."""
    out, seen = [], set()
    for t in tracks:
        tid = twin_of(t)
        if tid:
            t = get_track(tid) or t
        if t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
    return out


def without_twins(tracks: list[dict]) -> list[dict]:
    """The (plugin) songs that are not in the library already: for a list that shows the library's songs as well."""
    return [t for t in tracks if not twin_of(t)]


def get_track(track_id: str) -> dict | None:
    with db.db() as c:
        r = c.execute("SELECT * FROM lib_tracks WHERE id = ?", (track_id,)).fetchone()
    return _track(r) if r else None


def file_path(track_id: str) -> Path | None:
    with db.db() as c:
        r = c.execute("SELECT path FROM lib_tracks WHERE id = ?", (track_id,)).fetchone()
    if not r:
        return None
    prefix, base = next(((x, d) for x, d in reversed(roots()) if x and r["path"].startswith(x)), ("", MUSIC_DIR))
    p = (base / r["path"].removeprefix(prefix)).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def cover_path(track_id: str) -> Path | None:
    with db.db() as c:
        r = c.execute("SELECT cover FROM lib_tracks WHERE id = ?", (track_id,)).fetchone()
    if not r or not r["cover"]:
        return None
    p = COVER_DIR / Path(r["cover"]).name
    return p if p.is_file() else None


def _terms(q: str) -> list[str]:
    return [t for t in _norm(q).split() if t][:6]


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_tracks(q: str, limit: int = 25) -> list[dict]:
    terms = _terms(q)
    if not terms:
        return []
    where = " AND ".join("search LIKE ? ESCAPE '\\'" for _ in terms)
    first = _norm(q)
    with db.db() as c:
        rows = c.execute(
            f"SELECT * FROM lib_tracks WHERE {where} "
            "ORDER BY (lower(title) = ?) DESC, (lower(title) LIKE ? ESCAPE '\\') DESC, title COLLATE NOCASE LIMIT ?",
            [_like(t) for t in terms] + [first, _like(first)[1:], limit],
        ).fetchall()
    return [_track(r) for r in rows]


def search_artists(q: str, limit: int = 25) -> list[dict]:
    terms = _terms(q)
    if not terms:
        return []
    where = " AND ".join("lower(album_artist) LIKE ? ESCAPE '\\'" for _ in terms)
    with db.db() as c:
        rows = c.execute(
            f"SELECT artist_id, album_artist, MAX(id) AS some, MAX(cover) AS cover FROM lib_tracks WHERE album_artist != ? AND {where} "
            "GROUP BY artist_id ORDER BY album_artist COLLATE NOCASE LIMIT ?", [UNKNOWN_ARTIST] + [_like(t) for t in terms] + [limit],
        ).fetchall()
    return [{"type": "artist", "id": r["artist_id"], "name": shown(r["album_artist"]),
             "thumb": _any_cover(r["artist_id"], "artist_id")} for r in rows]


def search_albums(q: str, limit: int = 25) -> list[dict]:
    terms = _terms(q)
    if not terms:
        return []
    where = " AND ".join("lower(album || ' ' || album_artist) LIKE ? ESCAPE '\\'" for _ in terms)
    with db.db() as c:
        rows = c.execute(
            f"SELECT album_id, album, album_artist, MAX(year) AS year FROM lib_tracks WHERE album_id != '' AND {where} "
            "GROUP BY album_id ORDER BY album COLLATE NOCASE LIMIT ?", [_like(t) for t in terms] + [limit],
        ).fetchall()
    return [_album_card(r) for r in rows]


def _any_cover(ref: str, column: str) -> str:
    with db.db() as c:
        r = c.execute(f"SELECT id FROM lib_tracks WHERE {column} = ? AND cover != '' ORDER BY disc_no, track_no LIMIT 1", (ref,)).fetchone()
    return f"/api/cover/{r['id']}" if r else ""


def _album_card(r) -> dict:
    return {"type": "album", "id": r["album_id"], "title": r["album"], "artist": shown(r["album_artist"]), "year": r["year"] or "",
            "kind": "Album", "thumb": _any_cover(r["album_id"], "album_id")}


def artist_page(artist_id: str) -> dict | None:
    with db.db() as c:
        rows = c.execute("SELECT * FROM lib_tracks WHERE artist_id = ? ORDER BY album COLLATE NOCASE, disc_no, track_no, title COLLATE NOCASE", (artist_id,)).fetchall()
        if not rows:
            return None
        albums = c.execute(
            "SELECT album_id, album, album_artist, MAX(year) AS year, COUNT(*) AS n FROM lib_tracks "
            "WHERE artist_id = ? AND album_id != '' GROUP BY album_id ORDER BY year DESC, album COLLATE NOCASE", (artist_id,)).fetchall()
    cards = [(_album_card(a), a["n"]) for a in albums]
    return {
        "id": artist_id, "name": shown(rows[0]["album_artist"]), "description": enrich.artist_description(artist_id),
        "subscribers": trn(len(rows), "{n} track", "{n} tracks"),
        "thumb": next((f"/api/cover/{r['id']}" for r in rows if r["cover"]), ""),
        "songs": [_track(r) for r in rows[:100]], "songs_browse_id": "",
        "albums": [card for card, n in cards if n > 2], "singles": [card for card, n in cards if n <= 2],
    }


def album_page(album_id: str) -> dict | None:
    with db.db() as c:
        rows = c.execute("SELECT * FROM lib_tracks WHERE album_id = ? ORDER BY disc_no, track_no, title COLLATE NOCASE", (album_id,)).fetchall()
    if not rows:
        return None
    total = sum(r["duration"] for r in rows)
    return {
        "id": album_id, "title": rows[0]["album"], "kind": "Album", "year": next((r["year"] for r in rows if r["year"]), ""),
        "duration": trn(max(1, round(total / 60)), "{n} minute", "{n} minutes"), "thumb": next((f"/api/cover/{r['id']}" for r in rows if r["cover"]), ""),
        "artist": shown(rows[0]["album_artist"]), "artist_id": rows[0]["artist_id"], "tracks": [_track(r) for r in rows],
    }


def recently_added(limit: int = 30) -> list[dict]:
    with db.db() as c:
        rows = c.execute("SELECT * FROM lib_tracks ORDER BY added DESC, album COLLATE NOCASE, disc_no, track_no LIMIT ?", (limit,)).fetchall()
    return [_track(r) for r in rows]


def random_tracks(limit: int = 30, exclude: set[str] | None = None) -> list[dict]:
    with db.db() as c:
        rows = c.execute("SELECT * FROM lib_tracks ORDER BY RANDOM() LIMIT ?", (limit + len(exclude or ()),)).fetchall()
    return [_track(r) for r in rows if r["id"] not in (exclude or set())][:limit]


# ---------- DJ fit: tempo and key (Camelot) ----------

def _tempo_diff(a: float, b: float) -> float:
    """Relative tempo difference; half/double tempo counts as matching (0 = identical)."""
    return min(abs(a * k - b) / b for k in (0.5, 1.0, 2.0))


def _key_dist(a: str, b: str) -> int | None:
    """Distance on the Camelot wheel: 0 = same, 1 = neighbour or major/minor pair, higher = further away."""
    try:
        na, nb = int(a[:-1]), int(b[:-1])
    except ValueError:
        return None
    step = min(abs(na - nb), 12 - abs(na - nb))
    if a[-1] == b[-1]:
        return step
    return 1 if step == 0 else step + 1  # 8A <-> 8B is a pair, otherwise major/minor change plus distance


def dj_affinity(a: dict, b: dict) -> float:
    """0..2.5: how well two analyzed tracks can be mixed into each other (tempo up to 1.5, key up to 1)."""
    s = 1.5 * max(0.0, 1 - _tempo_diff(a["bpm"], b["bpm"]) / 0.12)
    kd = _key_dist(a["key"], b["key"]) if a.get("key") and b.get("key") else None
    return s + (max(0.0, 1 - kd / 3) if kd is not None else 0.0)


def dj_order(tracks: list[dict], seed_id: str) -> list[dict]:
    """Orders analyzed tracks into a chain with transitions as smooth as possible (start: fitting the seed track).

    Tracks without analysis keep their place; only the analyzed ones swap places among themselves.
    """
    info = db.get_bpm_many([seed_id] + [t["id"] for t in tracks])
    slots = [i for i, t in enumerate(tracks) if t["id"] in info]
    if len(slots) < 3 or seed_id not in info:
        return tracks
    pool = [tracks[i] for i in slots]
    cur, chain = info[seed_id], []
    while pool:
        best = max(pool, key=lambda t: dj_affinity(cur, info[t["id"]]) - 0.01 * pool.index(t))  # on a tie: original order
        pool.remove(best)
        chain.append(best)
        cur = info[best["id"]]
    out = tracks[:]
    for i, t in zip(slots, chain):
        out[i] = t
    return out


def _similarity(seed, r, dj: dict, dice=random) -> dict:
    """The parts a candidate's score is made of (see `similar`); `dj` is None when the pair is not analyzed."""
    parts = {"random": dice.random() * 2, "dj": None}
    if seed["id"] in dj and r["id"] in dj:  # only where both are analyzed (DJ mode / background analysis)
        parts["dj"] = dj_affinity(dj[seed["id"]], dj[r["id"]]) * 0.8
    parts["artist"] = 3 if r["artist_id"] == seed["artist_id"] else 0
    parts["album"] = 2 if seed["album_id"] and r["album_id"] == seed["album_id"] else 0
    parts["genre"] = 2 if seed["genre"] and r["genre"] == seed["genre"] else 0
    try:
        parts["year"] = max(0, 1.5 - abs(int(r["year"]) - int(seed["year"])) / 5)
    except ValueError:
        parts["year"] = 0
    return parts


def similar(seed_id: str, limit: int = 40, explain: bool = False, dice=random) -> list[dict]:
    """Radio/mix: tracks by the same artist, album and genre, with a dice roll so it is not the same every time.

    explain=True adds "dev" to every track (developer tools): the parts of its score, its rank and the score of the first track that did not make it.
    dice: where the dice roll comes from; the developer tools pass a `random.Random` with a fixed start so that a result can be repeated."""
    with db.db() as c:
        seed = c.execute("SELECT * FROM lib_tracks WHERE id = ?", (seed_id,)).fetchone()
        if seed is None:
            return []
        rows = c.execute(
            "SELECT * FROM lib_tracks WHERE id != ? AND (artist_id = ? OR (album_id != '' AND album_id = ?) OR (genre != '' AND genre = ?) "
            "OR (year != '' AND year = ?)) LIMIT 600",
            (seed_id, seed["artist_id"], seed["album_id"], seed["genre"], seed["year"]),
        ).fetchall()
    dj = db.get_bpm_many([seed_id] + [r["id"] for r in rows])
    scored = []
    for r in rows:
        parts = _similarity(seed, r, dj, dice)
        scored.append((sum(v for v in parts.values() if v is not None), parts, r))
    ranked = sorted(scored, key=lambda x: x[0], reverse=True)
    cutoff = ranked[limit][0] if len(ranked) > limit else None
    out = []
    for rank, (total, parts, r) in enumerate(ranked[:limit], 1):
        t = _track(r)
        if explain:
            t["dev"] = {"kind": "similar", "seed": seed_id, "score": round(total, 2), "rank": rank, "candidates": len(ranked),
                        "cutoff": None if cutoff is None else round(cutoff, 2),
                        "parts": {k: None if v is None else round(v, 2) for k, v in parts.items()}}
        out.append(t)
    if len(out) < limit:  # small library: fill up with random tracks
        have = {t["id"] for t in out} | {seed_id}
        filler = random_tracks(limit - len(out), have)
        if explain:
            for t in filler:
                t["dev"] = {"kind": "filler", "seed": seed_id}
        out += filler
    return out


def suggest(q: str, limit: int = 8) -> list[str]:
    """Suggestions while typing: titles, artists and albums that start with the input (first) or contain it."""
    t = _norm(q)
    if not t:
        return []
    starts, contains = _like(t)[1:], _like(t)
    out: list[str] = []
    with db.db() as c:
        for col in ("title", "album_artist", "album"):
            for pat in (starts, contains):
                for r in c.execute(f"SELECT DISTINCT {col} AS v FROM lib_tracks WHERE {col} != '' AND lower({col}) LIKE ? ESCAPE '\\' LIMIT ?", (pat, limit)):
                    if r["v"] not in out:
                        out.append(r["v"])
    return out[:limit]


def stats() -> dict:
    with db.db() as c:
        r = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b, COALESCE(SUM(duration), 0) AS s, "
                      "COUNT(DISTINCT artist_id) AS artists, COUNT(DISTINCT NULLIF(album_id, '')) AS albums FROM lib_tracks").fetchone()
    return {"tracks": r["n"], "bytes": r["b"], "seconds": r["s"], "artists": r["artists"], "albums": r["albums"],
            "music_dir": str(MUSIC_DIR), "scan": scan_state()}
