"""Metadata enrichment: release year, cover and a short description from MusicBrainz and the Cover Art Archive.

Supplementary only: whatever tags, path or folder image already provide stays unchanged. Only artist and album names are
looked up (nothing else). Hits and misses are stored so the same question is not asked again and again.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import db
from i18n import tr

log = logging.getLogger("enrich")

MB_URL = "https://musicbrainz.org/ws/2/"
CAA_URL = "https://coverartarchive.org/release-group/"
USER_AGENT = f"{os.getenv('APP_NAME', 'NxtSound')} (self-hosted music server)"
MIN_GAP = 1.1  # MusicBrainz allows about one request per second
RETRY_AFTER = 30 * 86400  # try misses again after 30 days
ENV_DEFAULT = os.getenv("ENRICH", "1") != "0"

_lock = threading.Lock()
_net_lock = threading.Lock()
_last_call = 0.0
_state = {"running": False, "started": 0.0, "finished": 0.0, "artists": 0, "albums": 0, "misses": 0, "error": ""}

_TYPES = {"Group": "Band", "Person": "Solo artist", "Orchestra": "Orchestra", "Choir": "Choir", "Character": "Character"}


class NetworkError(Exception):
    """MusicBrainz or the Cover Art Archive is unreachable (not a miss, try again later)."""


# ---------- On/off ----------

def enabled() -> bool:
    """The admin setting takes precedence over the ENRICH environment variable."""
    v = db.get_setting("enrich", "")
    return (v == "1") if v in ("0", "1") else ENV_DEFAULT


def set_enabled(on: bool) -> None:
    db.set_setting("enrich", "1" if on else "0")


# ---------- Network ----------

def _fetch(url: str, accept: str = "application/json") -> tuple[bytes, str] | None:
    """GET with a pause between requests. None on 404, NetworkError for anything else unexpected (503 retried once)."""
    global _last_call
    with _net_lock:
        for attempt in (1, 2):
            wait = _last_call + MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
                with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (fixed https addresses)
                    return r.read(8_000_000), r.headers.get_content_type()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code == 503 and attempt == 1:  # MusicBrainz also uses this for "too many requests"
                    time.sleep(3)
                    continue
                raise NetworkError(f"HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise NetworkError(str(e)) from e
            finally:
                _last_call = time.monotonic()
    raise NetworkError("no response")


def _mb(entity: str, query: str) -> list[dict]:
    res = _fetch(MB_URL + entity + "?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 5}))
    if not res:
        return []
    key = {"artist": "artists", "release-group": "release-groups"}[entity]
    try:
        return json.loads(res[0].decode("utf-8")).get(key, [])
    except ValueError as e:
        raise NetworkError("invalid response") from e


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _same(a: str, b: str) -> bool:
    simple = lambda s: re.sub(r"[^\w]+", "", s.lower())  # noqa: E731
    return bool(simple(a)) and simple(a) == simple(b)


# ---------- Lookup ----------

def artist_facts(hit: dict) -> dict:
    """Language-neutral facts from the MusicBrainz fields. These are stored; `describe` renders them in the reader's language."""
    span = hit.get("life-span") or {}
    tags = sorted(hit.get("tags") or [], key=lambda t: -t.get("count", 0))[:4]
    return {"type": hit.get("type") or "", "area": (hit.get("area") or {}).get("name") or "",
            "begin": (span.get("begin") or "")[:4], "end": (span.get("end") or "")[:4],
            "note": str(hit.get("disambiguation") or "").strip(), "genres": [t["name"] for t in tags]}


def describe(facts: dict) -> str:
    """Short text from the stored facts: 'Band from Germany, 1990–2005. Genres: rock, pop.' (translated)."""
    kind = tr(_TYPES.get(facts.get("type") or "", "Artist"))
    first = tr("{kind} from {area}", kind=kind, area=facts["area"]) if facts.get("area") else kind
    begin, end = facts.get("begin", ""), facts.get("end", "")
    if begin and end:
        first += f", {begin}–{end}"
    elif begin:
        first += ", " + tr("since {year}", year=begin)
    parts = [first]
    if facts.get("note"):
        parts.append(facts["note"].capitalize())
    if facts.get("genres"):
        parts.append(tr("Genres: {genres}", genres=", ".join(facts["genres"])))
    return ". ".join(parts) + "."


def lookup_artist(name: str) -> dict | None:
    for hit in _mb("artist", f"artist:{_q(name)}"):
        if hit.get("score", 0) >= 90 and _same(hit.get("name", ""), name):
            return {"mbid": hit["id"], "facts": artist_facts(hit)}
    return None


def lookup_album(artist: str, album: str) -> dict | None:
    for hit in _mb("release-group", f"releasegroup:{_q(album)} AND artist:{_q(artist)}"):
        if hit.get("score", 0) < 90 or not _same(hit.get("title", ""), album):
            continue
        credit = " ".join(c.get("name", "") for c in hit.get("artist-credit", []) if isinstance(c, dict))
        if credit and not (_same(credit, artist) or _same(credit.split(",")[0], artist)):
            continue
        out = {"mbid": hit["id"], "year": (hit.get("first-release-date") or "")[:4], "cover": b"", "mime": ""}
        img = _fetch(CAA_URL + hit["id"] + "/front-500", "image/*")
        if img and img[1].startswith("image/") and img[0]:
            out["cover"], out["mime"] = img
        return out
    return None


# ---------- Storage ----------

def cached_album(album_id: str) -> tuple[str, str]:
    """(year, cover file) from earlier hits, for newly scanned files without details of their own."""
    if not album_id:
        return "", ""
    with db.db() as c:
        r = c.execute("SELECT year, cover FROM meta_album WHERE album_id = ? AND found = 1", (album_id,)).fetchone()
    return (r["year"], r["cover"]) if r else ("", "")


def artist_description(artist_id: str) -> str:
    """Description of an artist in the request language ('' if unknown). The column holds JSON facts (older rows: plain text)."""
    with db.db() as c:
        r = c.execute("SELECT description FROM meta_artist WHERE artist_id = ? AND found = 1", (artist_id,)).fetchone()
    if not r or not r["description"]:
        return ""
    try:
        facts = json.loads(r["description"])
        return describe(facts) if isinstance(facts, dict) else r["description"]
    except ValueError:
        return r["description"]


def _save_artist(artist_id: str, hit: dict | None) -> None:
    with db.db() as c:
        c.execute("INSERT OR REPLACE INTO meta_artist(artist_id, mbid, description, found, fetched_at) VALUES(?,?,?,?,?)",
                  (artist_id, (hit or {}).get("mbid", ""), json.dumps((hit or {}).get("facts") or {}) if hit else "", 1 if hit else 0, int(time.time())))


def _save_album(album_id: str, hit: dict | None) -> None:
    import library
    cover = library._store_cover(hit["cover"], hit["mime"]) if hit and hit["cover"] else ""
    year = hit["year"] if hit else ""
    with db.db() as c:
        c.execute("INSERT OR REPLACE INTO meta_album(album_id, mbid, year, cover, found, fetched_at) VALUES(?,?,?,?,?,?)",
                  (album_id, (hit or {}).get("mbid", ""), year, cover, 1 if hit else 0, int(time.time())))
        if year:
            c.execute("UPDATE lib_tracks SET year = ? WHERE album_id = ? AND year = ''", (year, album_id))
        if cover:
            c.execute("UPDATE lib_tracks SET cover = ? WHERE album_id = ? AND cover = ''", (cover, album_id))


# ---------- Lauf ----------

def _todo(retry: bool) -> tuple[list, list]:
    import library
    cutoff = 0 if retry else int(time.time()) - RETRY_AFTER
    with db.db() as c:
        artists = c.execute(
            "SELECT DISTINCT artist_id, album_artist FROM lib_tracks WHERE album_artist != ? AND artist_id NOT IN "
            "(SELECT artist_id FROM meta_artist WHERE found = 1 OR fetched_at > ?) ORDER BY album_artist", (library.UNKNOWN_ARTIST, cutoff)).fetchall()
        albums = c.execute(
            "SELECT album_id, album, album_artist FROM lib_tracks WHERE album != '' AND album_artist != ? "
            "AND album_id NOT IN (SELECT album_id FROM meta_album WHERE found = 1 OR fetched_at > ?) "
            "GROUP BY album_id HAVING MAX(year) = '' OR MAX(cover) = '' ORDER BY album", (library.UNKNOWN_ARTIST, cutoff)).fetchall()
    return artists, albums


def run(retry: bool = False) -> dict:
    """Looks up missing artists and albums (blocking; call from a background thread)."""
    if not _lock.acquire(blocking=False):
        return status()
    try:
        _state.update(running=True, started=time.time(), artists=0, albums=0, misses=0, error="")
        artists, albums = _todo(retry)
        log.info("Enrichment run started: %d artists, %d albums to look up", len(artists), len(albums))
        try:
            for r in albums:
                if not enabled():
                    break
                hit = lookup_album(r["album_artist"], r["album"])
                _save_album(r["album_id"], hit)
                _state["albums" if hit else "misses"] += 1
            for r in artists:
                if not enabled():
                    break
                hit = lookup_artist(r["album_artist"])
                _save_artist(r["artist_id"], hit)
                _state["artists" if hit else "misses"] += 1
        except NetworkError as e:
            _state["error"] = str(e)
            log.warning("Enrichment aborted: %s", e)
        log.info("Enrichment run finished: %d artists and %d albums found, %d without match", _state["artists"], _state["albums"], _state["misses"])
        return status()
    finally:
        _state.update(running=False, finished=time.time())
        _lock.release()


def maybe_run(retry: bool = False) -> bool:
    """Starts a run in the background if enabled and none is running."""
    if not enabled() or _state["running"] or _lock.locked():
        return False
    _state["running"] = True
    threading.Thread(target=run, args=(retry,), daemon=True, name="enrich").start()
    return True


def status() -> dict:
    with db.db() as c:
        a = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(found), 0) AS f FROM meta_artist").fetchone()
        b = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(found), 0) AS f FROM meta_album").fetchone()
    return {"enabled": enabled(), "from_admin": db.get_setting("enrich", "") in ("0", "1"), "source": "MusicBrainz, Cover Art Archive",
            "artists_found": a["f"], "artists_missed": a["n"] - a["f"], "albums_found": b["f"], "albums_missed": b["n"] - b["f"],
            **_state}
