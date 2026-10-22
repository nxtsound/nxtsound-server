"""The widgets of the home page: what exists (the catalog with its options), the default layout, checking a layout a user saved, and building the data of each widget.

A layout is an ordered list of entries {"id", "type", "opts"}. Users change it (order, add, remove, options); without one they get DEFAULT_LAYOUT.
Texts are translated when they are asked for, so the catalog and the widgets come in the language of the request. Widgets that have nothing to show
(no history yet, no likes, community off) are simply left out of the answer."""
import asyncio
import json
import re
import secrets
import time
from typing import Awaitable, Callable

import autolists
import db
import library
import plugins
from i18n import tr, trn

MAX_WIDGETS = 30
_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_TRACKS_PER_SHELF = 10


def _int(key: str, label: str, lo: int, hi: int, default: int) -> dict:
    return {"key": key, "type": "int", "label": label, "min": lo, "max": hi, "default": default}


def _choice(key: str, label: str, choices: list[tuple[str, str]], default: str) -> dict:
    return {"key": key, "type": "choice", "label": label, "default": default, "choices": [{"value": v, "label": t} for v, t in choices]}


def _periods() -> list[tuple[str, str]]:
    return [("week", tr("7 days")), ("month", tr("30 days")), ("year", tr("Year")), ("all", tr("All time"))]


def specs() -> dict[str, dict]:
    """type -> {title, description, multiple (may be added more than once), options}. In the order of the "add" list."""
    return {
        "recent": {"title": tr("Recently played"), "description": tr("The songs you played last, a few at a time."), "multiple": False,
                   "options": [_int("pages", tr("Pages"), 1, 3, 3)]},
        "new": {"title": tr("Recently added"), "description": tr("The newest songs in your library."), "multiple": False,
                "options": [_int("count", tr("Songs"), 6, 24, 8)]},
        "mix": {"title": tr("Your Mix"), "description": tr("A mix of songs similar to what you listened to recently."), "multiple": False, "options": []},
        "because": {"title": tr("Because you listened to …"), "description": tr("Songs similar to ones you played often, one row per song."), "multiple": False,
                    "options": [_int("rows", tr("Rows"), 1, 5, 1)]},
        "friends": {"title": tr("What your friends listen to"), "description": tr("What the people you follow listen to (needs Community)."), "multiple": False, "options": []},
        "artists": {"title": tr("Your artists"), "description": tr("The artists you listen to most."), "multiple": False,
                    "options": [_int("count", tr("Artists"), 6, 20, 8)]},
        "rediscover": {"title": tr("Rediscover"), "description": tr("Songs you have not heard for a while, but not forgotten."), "multiple": False,
                       "options": [_int("count", tr("Songs"), 6, 24, 12)]},
        "top": {"title": tr("Your top songs"), "description": tr("The songs you played most, for a period you choose."), "multiple": True,
                "options": [_choice("period", tr("Period"), _periods(), "month"), _int("count", tr("Songs"), 6, 24, 12)]},
        "genres": {"title": tr("Genres"), "description": tr("Mixes by genre from your library."), "multiple": False, "options": []},
        "decades": {"title": tr("Decades"), "description": tr("Mixes by decade from your library."), "multiple": False, "options": []},
        "artistmix": {"title": tr("Artist mixes"), "description": tr("A mix for each of your favourite artists."), "multiple": False, "options": []},
        "discover": {"title": tr("Discover"), "description": tr("A random selection from your library."), "multiple": False,
                     "options": [_int("count", tr("Songs"), 6, 24, 12)]},
        "playlists": {"title": tr("Your playlists"), "description": tr("Your newest playlists at a glance."), "multiple": False,
                      "options": [_int("count", tr("Playlists"), 3, 20, 10)]},
        "likes": {"title": tr("Shuffle your liked songs"), "description": tr("One tap to play your liked songs in random order."), "multiple": False, "options": []},
        "stats": {"title": tr("Recap in numbers"), "description": tr("How much you listened, for the last week or month."), "multiple": True,
                  "options": [_choice("period", tr("Period"), [("week", tr("7 days")), ("month", tr("30 days"))], "week")]},
        "onthisday": {"title": tr("A year ago"), "description": tr("What you listened to around this time last year."), "multiple": False, "options": []},
        "timeofday": {"title": tr("Your mix for this time of day"), "description": tr("Songs you like to hear at this time of day."), "multiple": False, "options": []},
    }


DEFAULT_LAYOUT = [{"id": t, "type": t, "opts": {}} for t in ("recent", "new", "mix", "friends", "because", "artists")]


def catalog() -> list[dict]:
    return [{"type": t, **s} for t, s in specs().items()]


def _clean_opts(spec: dict, raw) -> dict:
    """Only the options this widget has, each inside its range; anything else becomes the default."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for o in spec["options"]:
        v = raw.get(o["key"], o["default"])
        if o["type"] == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = o["default"]
            v = min(o["max"], max(o["min"], v))
        elif v not in {c["value"] for c in o["choices"]}:
            v = o["default"]
        out[o["key"]] = v
    return out


def clean_layout(raw, strict: bool = False) -> list[dict]:
    """A layout that is safe to store and build. strict: raise ValueError for what is wrong (the API); otherwise skip it (stored layouts of other versions)."""
    all_specs = specs()
    if not isinstance(raw, list):
        raise ValueError("layout must be a list")
    out, seen_types, seen_ids = [], set(), set()
    for e in raw:
        t = e.get("type") if isinstance(e, dict) else None
        if t not in all_specs:
            if strict:
                raise ValueError(f"unknown widget: {t}")
            continue
        if not all_specs[t]["multiple"] and t in seen_types:
            if strict:
                raise ValueError(f"widget only once: {t}")
            continue
        wid = e.get("id")
        if not isinstance(wid, str) or not _ID.match(wid) or wid in seen_ids:
            wid = t if (t not in seen_ids and not all_specs[t]["multiple"]) else f"{t}-{secrets.token_hex(3)}"
        seen_types.add(t)
        seen_ids.add(wid)
        out.append({"id": wid, "type": t, "opts": _clean_opts(all_specs[t], e.get("opts"))})
    if len(out) > MAX_WIDGETS:
        if strict:
            raise ValueError("too many widgets")
        out = out[:MAX_WIDGETS]
    return out


def layout_of(user_id: int) -> tuple[list[dict], bool, int]:
    """(the user's layout, whether it is their own, time of the last change). A broken stored layout counts as none."""
    stored = db.get_home_layout(user_id)
    if stored:
        try:
            return clean_layout(json.loads(stored[0])), True, stored[1]
        except (ValueError, TypeError):
            pass
    return clean_layout(DEFAULT_LAYOUT), False, 0


def save_layout(user_id: int, layout: list[dict]) -> None:
    db.set_home_layout(user_id, json.dumps(layout, separators=(",", ":")))


# ---------- Building ----------

Allowed = Callable[[list[dict]], list[dict]]
_similar_cache: dict[int, tuple[int, float, int, list, list]] = {}  # user -> (last play ID, time, seeds, seed songs, similar lists): the slow part of "Your Mix"
SIMILAR_TTL = 900


class _Build:
    """Things several widgets need are worked out once per home page, and only if a widget asks."""

    def __init__(self, user_id: int, tz: int, allowed: Allowed, refresh: bool, seeds: int):
        self.uid, self.tz, self.allowed, self.refresh, self.need_seeds = user_id, tz, allowed, refresh, seeds
        self._recent: list[dict] | None = None
        self._similar: tuple[list[dict], list[list[dict]]] | None = None
        self._lists: list[dict] | None = None
        self._candidates: list[dict] = []
        self.has_new = False

    def alive(self, tracks: list[dict]) -> list[dict]:
        """Without songs that are gone (not in the library any more, plugin off)."""
        ids = [t["id"] for t in tracks]
        ok = library.existing(ids) | {i for i in ids if plugins.is_enabled_id(i)}
        return [t for t in tracks if t["id"] in ok]

    async def recent(self) -> list[dict]:
        if self._recent is None:
            self._candidates = await asyncio.to_thread(db.seed_candidates, self.uid, 6)
            recent = await asyncio.to_thread(db.recent_tracks, self.uid, 20)
            ids = [t["id"] for t in recent + self._candidates]
            ok = (await asyncio.to_thread(library.existing, ids)) | {i for i in ids if plugins.is_enabled_id(i)}
            self._alive_ids = ok
            self._recent = [t for t in recent if t["id"] in ok]
        return self._recent

    async def known(self) -> set[str]:
        return {t["id"] for t in await self.recent()}

    async def similar(self) -> tuple[list[dict], list[list[dict]]]:
        """(seed songs, for each the similar songs the user has not heard recently)."""
        if self._similar is None:
            await self.recent()
            latest = await asyncio.to_thread(db.latest_play_id, self.uid)
            hit = _similar_cache.get(self.uid)
            if not self.refresh and hit and hit[0] == latest and time.time() - hit[1] < SIMILAR_TTL and hit[2] >= self.need_seeds:
                self._similar = (hit[3], hit[4])
            else:
                blocked = await asyncio.to_thread(db.blocked_sets, self.uid)
                seeds = [s for s in self._candidates if s["id"] in self._alive_ids and self.allowed([s])][: self.need_seeds]

                async def similar_to(seed: dict) -> list[dict]:
                    if plugins.is_plugin_id(seed["id"]):  # a plugin song: the source's radio (slow, hence in parallel)
                        try:
                            return await asyncio.to_thread(plugins.radio, seed["id"], 30)
                        except plugins.PluginError:
                            return []
                    return await asyncio.to_thread(library.similar, seed["id"], 30)

                results = [self.allowed(r) for r in await asyncio.gather(*(similar_to(s) for s in seeds))]
                known = await self.known()
                self._similar = (seeds, [[t for t in r if t["id"] not in known] for r in results])
                _similar_cache[self.uid] = (latest, time.time(), self.need_seeds, *self._similar)
        return self._similar

    async def lists(self) -> list[dict]:
        if self._lists is None:
            self._lists = await asyncio.to_thread(autolists.index, self.uid, self.allowed)
        return self._lists


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


def _w(entry: dict, view: str, title: str, subtitle: str = "", **data) -> dict:
    return {"id": entry["id"], "type": entry["type"], "view": view, "title": title, "subtitle": subtitle, **data}


async def _recent(b: _Build, e: dict, o: dict):
    tracks = (await b.recent())[: o["pages"] * 4]
    return _w(e, "pager", tr("Recently played"), tracks=tracks) if tracks else None


async def _new(b: _Build, e: dict, o: dict):
    known = await b.known()
    fresh = await asyncio.to_thread(library.recently_added, o["count"] + len(known))
    tracks = [t for t in fresh if t["id"] not in known][: o["count"]]
    return _w(e, "shelf", tr("Recently added"), tracks=tracks, nav="#/auto/new") if tracks else None


async def _mix(b: _Build, e: dict, o: dict):
    _, lists = await b.similar()
    mix = _interleave(lists[:3], 20)
    return _w(e, "hero", tr("Your Mix"), tr("Based on what you listened to recently"), tracks=mix) if mix else None


async def _because(b: _Build, e: dict, o: dict):
    seeds, lists = await b.similar()
    out = []
    for n, (seed, tracks) in enumerate(zip(seeds, lists)):
        if n >= o["rows"]:
            break
        if tracks[:_TRACKS_PER_SHELF]:
            out.append(_w({"id": f"{e['id']}-{n}", "type": e["type"]}, "because", tr("Because you listened to “{title}”", title=seed["title"]), seed["artist"],
                          thumb=seed.get("thumb", ""), tracks=tracks[:_TRACKS_PER_SHELF]))
    return out


async def _friends(b: _Build, e: dict, o: dict):
    if not db.community_enabled():
        return None
    items = await b.friends()
    return _w(e, "friends", tr("What your friends listen to"), items=items, following=len(db.following_ids(b.uid)))


async def _artists(b: _Build, e: dict, o: dict):
    artists = await asyncio.to_thread(db.top_artists, b.uid, o["count"])
    return _w(e, "artists", tr("Your artists"), artists=artists) if artists else None


async def _rediscover(b: _Build, e: dict, o: dict):
    res = await asyncio.to_thread(autolists.get, b.uid, "rediscover", b.allowed)
    tracks = b.alive(res["tracks"])[: o["count"]] if res else []
    return _w(e, "shelf", tr("Rediscover"), tr("Not heard for a while, but not forgotten"), tracks=tracks, nav="#/auto/rediscover") if len(tracks) >= 3 else None


async def _top(b: _Build, e: dict, o: dict):
    period = o["period"]
    if period == "all":
        tracks = await asyncio.to_thread(db.top_tracks, b.uid, o["count"] * 2)
    else:
        tracks = await asyncio.to_thread(db.top_tracks_since, b.uid, time.time() - db.RECAP_DAYS[period] * 86400, o["count"] * 2)
    tracks = b.alive(b.allowed(tracks))[: o["count"]]
    titles = {"week": tr("Top songs this week"), "month": tr("Top songs this month"), "year": tr("Top songs this year"), "all": tr("Your most played songs")}
    return _w(e, "shelf", titles[period], tracks=tracks) if len(tracks) >= 3 else None


def _tiles(title: str, prefix: str):
    async def build(b: _Build, e: dict, o: dict):
        items = [x for x in await b.lists() if x["id"].startswith(prefix)]
        return _w(e, "tiles", title, items=items) if items else None
    return build


async def _discover(b: _Build, e: dict, o: dict):
    skip = set(await b.known())
    if b.has_new:  # not the songs the "recently added" row shows anyway
        skip |= {t["id"] for t in await asyncio.to_thread(library.recently_added, 12)}
    tracks = await asyncio.to_thread(library.random_tracks, o["count"], skip)
    return _w(e, "shelf", tr("Discover"), tr("A random selection from your library"), tracks=tracks) if tracks else None


async def _playlists(b: _Build, e: dict, o: dict):
    items = (await asyncio.to_thread(db.list_playlists, b.uid))[: o["count"]]
    return _w(e, "playlists", tr("Your playlists"), items=items, nav="#/playlists") if items else None


async def _likes(b: _Build, e: dict, o: dict):
    n = len(await asyncio.to_thread(db.list_likes, b.uid, 500))
    return _w(e, "action", tr("Shuffle your liked songs"), trn(n, "{n} song you liked", "{n} songs you liked"), action="shuffleLikes", count=n) if n else None


async def _stats(b: _Build, e: dict, o: dict):
    period = o["period"]
    r = await asyncio.to_thread(db.recap, b.uid, period, b.tz)
    if not r["plays"]:
        return None
    title = tr("Your week in numbers") if period == "week" else tr("Your month in numbers")
    return _w(e, "stats", title, seconds=r["seconds"], plays=r["plays"], top_artist=r["top_artists"][0]["name"] if r["top_artists"] else "", nav=f"#/account/recap?p={period}")


async def _onthisday(b: _Build, e: dict, o: dict):
    year_ago = time.time() - 365 * 86400
    plays = await asyncio.to_thread(db.plays_in_range, b.uid, year_ago - 3 * 86400, year_ago + 4 * 86400)
    seen, tracks = set(), []
    for p in plays:
        if p["id"] not in seen:
            seen.add(p["id"])
            tracks.append({k: v for k, v in p.items() if k != "played_at"})
    tracks = b.alive(b.allowed(tracks))[:_TRACKS_PER_SHELF]
    return _w(e, "shelf", tr("A year ago"), tr("What you listened to around this time last year"), tracks=tracks) if len(tracks) >= 3 else None


_PARTS = (  # (first hour, last hour excluded); the night, 22 to 5, is what is left
    (5, 11), (11, 17), (17, 22),
)


async def _timeofday(b: _Build, e: dict, o: dict):
    now = time.time()
    hour = int(((now + b.tz * 60) % 86400) // 3600)
    lo, hi = next(((a, z) for a, z in _PARTS if a <= hour < z), (22, 29))  # the night runs past midnight: 22 to 5
    title = {5: tr("Your morning mix"), 11: tr("Your afternoon mix"), 17: tr("Your evening mix"), 22: tr("Your night mix")}[lo]
    plays = await asyncio.to_thread(db.plays_in_range, b.uid, now - 90 * 86400, now)
    counts: dict[str, int] = {}
    first: dict[str, dict] = {}
    for p in plays:
        h = int(((p["played_at"] + b.tz * 60) % 86400) // 3600)
        if lo <= h < hi or (hi > 24 and (h >= lo or h < hi - 24)):
            counts[p["id"]] = counts.get(p["id"], 0) + 1
            first.setdefault(p["id"], {k: v for k, v in p.items() if k != "played_at"})
    ranked = sorted(counts, key=lambda i: -counts[i])
    tracks = b.alive(b.allowed([first[i] for i in ranked]))[:_TRACKS_PER_SHELF]
    return _w(e, "shelf", title, tr("Songs you like to hear at this time of day"), tracks=tracks) if len(tracks) >= 4 else None


_BUILDERS: dict[str, Callable[[_Build, dict, dict], Awaitable]] = {
    "recent": _recent, "new": _new, "mix": _mix, "because": _because, "friends": _friends, "artists": _artists, "rediscover": _rediscover, "top": _top,
    "discover": _discover, "playlists": _playlists, "likes": _likes, "stats": _stats, "onthisday": _onthisday, "timeofday": _timeofday,
}


async def build(user_id: int, layout: list[dict], tz: int, allowed: Allowed, friends: Callable[[int], Awaitable[list[dict]]], refresh: bool = False) -> list[dict]:
    """The widgets of a layout with their data, in the layout's order. Widgets without content are left out."""
    seeds = max([3] + [e["opts"].get("rows", 1) for e in layout if e["type"] == "because"])
    b = _Build(user_id, tz, allowed, refresh, seeds)
    b.friends = lambda: friends(user_id)
    b.has_new = any(e["type"] == "new" for e in layout)
    builders = dict(_BUILDERS)
    builders["genres"] = _tiles(tr("Genres"), "genre:")
    builders["decades"] = _tiles(tr("Decades"), "decade:")
    builders["artistmix"] = _tiles(tr("Artist mixes"), "artist:")
    out: list[dict] = []
    for e in layout:
        res = await builders[e["type"]](b, e, e["opts"])
        if res:
            out.extend(res if isinstance(res, list) else [res])
    return out


def forget(user_id: int) -> None:
    _similar_cache.pop(user_id, None)
