"""Automatic playlists ("For you"): computed on every request from listening history, likes and library.

None of this is stored; the lists change with the listening behaviour. Mixes are stable per day
(same user, same list, same day = same order).
"""
import random
import time

import db
import library
import plugins
from i18n import tr

MIN_TRACKS = 3  # smaller lists are not shown
MAX_TRACKS = 50
FORGOTTEN_AFTER = 30 * 86400  # "Rediscover": not listened to for this long


def _shuffled(user_id: int, lid: str, tracks: list[dict]) -> list[dict]:
    rng = random.Random(f"{user_id}:{lid}:{time.strftime('%Y-%m-%d')}")
    out = tracks[:]
    rng.shuffle(out)
    return out


def tracks_by_ids(ids: list[str]) -> list[dict]:
    """Library tracks in the order of the IDs (unknown IDs are dropped)."""
    if not ids:
        return []
    with db.db() as c:
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in c.execute(f"SELECT * FROM lib_tracks WHERE id IN ({marks})", ids)}
    return [library._track(rows[i]) for i in ids if i in rows]


def _hydrate(user_id: int, ids: list[str]) -> list[dict]:
    """Track data in the order of the IDs: library from the database, plugin tracks (of active plugins) from the remembered history."""
    active = {p["id"] for p in plugins.enabled_plugins()}
    plug_ids = [i for i in ids if (plugins.split_id(i) or ("",))[0] in active]
    lib = {t["id"]: t for t in tracks_by_ids([i for i in ids if not plugins.is_plugin_id(i)])}
    lib.update(db.known_tracks(user_id, plug_ids))
    return [lib[i] for i in ids if i in lib]


def _top(user_id: int) -> list[dict]:
    with db.db() as c:
        ids = [r["video_id"] for r in c.execute(
            "SELECT video_id FROM plays WHERE user_id = ? GROUP BY video_id ORDER BY COUNT(*) DESC, MAX(id) DESC LIMIT ?", (user_id, MAX_TRACKS * 2))]
    return _hydrate(user_id, ids)[:MAX_TRACKS]


def _rediscover(user_id: int) -> list[dict]:
    cutoff = time.time() - FORGOTTEN_AFTER
    with db.db() as c:
        played = [(r["video_id"], r["last"]) for r in c.execute(
            "SELECT video_id, MAX(played_at) AS last FROM plays WHERE user_id = ? GROUP BY video_id ORDER BY COUNT(*) DESC", (user_id,))]
        recent = {v for v, last in played if last >= cutoff}
        liked = [r["video_id"] for r in c.execute("SELECT video_id FROM likes WHERE user_id = ? ORDER BY id DESC", (user_id,))]
    ids = [v for v, last in played if last < cutoff]
    ids += [v for v in liked if v not in recent and v not in ids]
    return _shuffled(user_id, "rediscover", _hydrate(user_id, ids[:MAX_TRACKS * 2]))[:MAX_TRACKS]


def _new(_user_id: int) -> list[dict]:
    return library.recently_added(MAX_TRACKS)


def _by_column(user_id: int, lid: str, where: str, arg: str) -> list[dict]:
    with db.db() as c:
        rows = c.execute(f"SELECT * FROM lib_tracks WHERE {where} LIMIT 500", (arg,)).fetchall()
    return _shuffled(user_id, lid, [library._track(r) for r in rows])[:MAX_TRACKS]


def _artist_mix(user_id: int, artist_id: str) -> list[dict]:
    with db.db() as c:
        seed = c.execute("SELECT video_id FROM plays WHERE user_id = ? AND artist_id = ? GROUP BY video_id ORDER BY COUNT(*) DESC LIMIT 1",
                         (user_id, artist_id)).fetchone()
        own = [library._track(r) for r in c.execute("SELECT * FROM lib_tracks WHERE artist_id = ? LIMIT 200", (artist_id,))]
    similar = library.similar(seed["video_id"], 30) if seed else []
    seen = {t["id"] for t in own}
    mix = own + [t for t in similar if t["id"] not in seen]
    return _shuffled(user_id, "artist:" + artist_id, mix)[:MAX_TRACKS]


def _plugin_artist_mix(user_id: int, artist_id: str, full: bool) -> tuple[str, list[dict]] | None:
    """Mix for a plugin artist: their played tracks plus the source's radio (only when opened, not in the overview)."""
    with db.db() as c:
        rows = c.execute("SELECT video_id, artist FROM plays WHERE user_id = ? AND artist_id = ? GROUP BY video_id ORDER BY COUNT(*) DESC, MAX(id) DESC",
                         (user_id, artist_id)).fetchall()
    if not rows:
        return None
    own = list(db.known_tracks(user_id, [r["video_id"] for r in rows]).values())
    name = rows[0]["artist"].split(", ")[0] or tr("Artist")
    if not full:
        return name, own
    try:
        radio = plugins.radio(rows[0]["video_id"], 30)
    except plugins.PluginError:
        radio = []
    seen = {t["id"] for t in own}
    return name, _shuffled(user_id, "artist:" + artist_id, own + [t for t in radio if t["id"] not in seen])[:MAX_TRACKS]


def _decade_name(d: str) -> str:
    n = int(d)
    return tr("The {decade}s", decade=f"{n % 100:02d}" if n < 2000 else str(n))


def _resolve(user_id: int, lid: str, full: bool = True) -> tuple[str, str, list[dict]] | None:
    """(name, subtitle, tracks) of an automatic list, or None."""
    kind, _, arg = lid.partition(":")
    if lid == "top":
        return tr("Most played"), tr("Your most frequently played tracks"), _top(user_id)
    if lid == "rediscover":
        return tr("Rediscover"), tr("Not heard for a while, but not forgotten"), _rediscover(user_id)
    if lid == "new":
        return tr("Recently added"), tr("Latest additions to the library"), _new(user_id)
    if kind == "genre" and arg:
        return tr("{genre} Mix", genre=arg), tr("From your library"), _by_column(user_id, lid, "genre = ?", arg)
    if kind == "decade" and arg.isdigit() and len(arg) == 4:
        return _decade_name(arg), tr("By release year"), _by_column(user_id, lid, "substr(year, 1, 3) = ?", arg[:3])
    if kind == "artist" and arg and plugins.is_plugin_id(arg):
        res = _plugin_artist_mix(user_id, arg, full)
        return (tr("Mix: {artist}", artist=res[0]), tr("The artist and similar"), res[1]) if res else None
    if kind == "artist" and arg:
        with db.db() as c:
            r = c.execute("SELECT album_artist FROM lib_tracks WHERE artist_id = ? LIMIT 1", (arg,)).fetchone()
        if r:
            return tr("Mix: {artist}", artist=library.shown(r["album_artist"])), tr("The artist and similar"), _artist_mix(user_id, arg)
    return None


def _candidates(user_id: int) -> list[str]:
    ids = ["top", "rediscover"]
    ids += [f"artist:{a['id']}" for a in db.top_artists(user_id, 3)]
    with db.db() as c:
        ids += [f"genre:{r['genre']}" for r in c.execute(
            "SELECT genre FROM lib_tracks WHERE genre != '' GROUP BY genre HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT 6", (MIN_TRACKS,))]
        ids += [f"decade:{r['d']}" for r in c.execute(
            "SELECT substr(year, 1, 3) || '0' AS d FROM lib_tracks WHERE year GLOB '[12][0-9][0-9][0-9]' "
            "GROUP BY d HAVING COUNT(*) >= ? ORDER BY d DESC LIMIT 5", (MIN_TRACKS,))]
    return ids + ["new"]


def index(user_id: int, allowed=lambda tracks: tracks) -> list[dict]:
    """All lists with enough tracks (`allowed` filters e.g. hidden tracks)."""
    out = []
    for lid in _candidates(user_id):
        res = _resolve(user_id, lid, full=False)
        if not res:
            continue
        name, sub, tracks = res[0], res[1], allowed(res[2])
        quick = lid.startswith("artist:") and plugins.is_plugin_id(lid[7:])  # a plugin mix is only filled with the source's radio when opened
        if len(tracks) >= MIN_TRACKS or (quick and tracks):
            out.append({"id": lid, "name": name, "subtitle": sub, "count": 0 if quick else len(tracks),
                        "thumb": next((t["thumb"] for t in tracks if t["thumb"]), "")})
    return out


def get(user_id: int, lid: str, allowed=lambda tracks: tracks) -> dict | None:
    res = _resolve(user_id, lid)
    if not res:
        return None
    return {"id": lid, "name": res[0], "subtitle": res[1], "tracks": allowed(res[2])}
