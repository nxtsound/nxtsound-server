"""Plugins: further music sources as separate services speaking the plugin protocol (see PLUGINS.md).

The core knows no concrete services. A plugin is an HTTP service; its address (and a shared secret) is registered, either by the
plugin host (folders on the server) or by the admin (remote plugin). The core reads the manifest, passes search, radio, stream,
artist and album pages on to the plugin and prefixes the IDs of plugin tracks with the plugin ID (`<plugin>:<id>`).
"""
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

import db
import i18n
import pluginproto
from i18n import tr

log = logging.getLogger("plugins")

from pluginproto import API_VERSION, FEATURES, ID_RE, KINDS, PluginError, validate_manifest  # noqa: F401 (part of this module's interface)

TIMEOUT = 10.0
MAX_ITEMS = 100

_transport_factory = None  # tests: returns an httpx transport per plugin (e.g. an ASGI app) instead of the real network
_lock = threading.Lock()


def set_transport_factory(factory) -> None:
    global _transport_factory
    _transport_factory = factory


# ---------- Verwaltung ----------

def _row(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "url": r["url"], "version": r["version"], "description": r["description"],
        "features": json.loads(r["features"] or "[]"), "search_kinds": json.loads(r["search_kinds"] or "[]"),
        "enabled": bool(r["enabled"]), "added_at": r["added_at"], "last_ok": r["last_ok"], "last_error": r["last_error"],
        "kind": r["kind"], "source": r["source"], "rev": r["rev"], "settings": json.loads(r["settings_schema"] or "[]"),
        "categories": json.loads(r["categories"] or "[]"), "actions": json.loads(r["actions"] or "[]"),
    }


def list_plugins() -> list[dict]:
    with db.db() as c:
        return [_row(r) for r in c.execute("SELECT * FROM plugins ORDER BY name COLLATE NOCASE")]


def get(plugin_id: str) -> dict | None:
    with db.db() as c:
        r = c.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
    return _row(r) if r else None


def _token(plugin_id: str) -> str:
    with db.db() as c:
        r = c.execute("SELECT token FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
    return r["token"] if r else ""


def enabled_plugins(feature: str = "") -> list[dict]:
    return [p for p in list_plugins() if p["enabled"] and (not feature or feature in p["features"])]


def is_plugin_id(ref: str) -> bool:
    return ":" in ref and not ref.startswith("local:")


def split_id(ref: str) -> tuple[str, str] | None:
    """'yt:abc' -> ('yt', 'abc'); local IDs and nonsense -> None."""
    if not is_plugin_id(ref):
        return None
    pid, _, local = ref.partition(":")
    return (pid, local) if ID_RE.match(pid) and local else None


def is_enabled_id(ref: str) -> bool:
    s = split_id(ref)
    if not s:
        return False
    p = get(s[0])
    return bool(p and p["enabled"])


# ---------- HTTP ----------

def _client(plugin_id: str, url: str, token: str) -> httpx.Client:
    headers = {"X-NxtSound-Api": str(API_VERSION), "User-Agent": "NxtSound-Server"}
    if token:
        headers["Authorization"] = "Bearer " + token
    kw = {"transport": _transport_factory(plugin_id)} if _transport_factory else {}
    return httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=TIMEOUT, follow_redirects=False, **kw)


def _call(plugin_id: str, path: str, params: dict | None = None, timeout: float | None = None, method: str = "GET", body: dict | None = None) -> dict:
    p = get(plugin_id)
    if not p or not p["enabled"]:
        raise PluginError("Plugin not available")
    try:
        with _client(plugin_id, p["url"], _token(plugin_id)) as c:
            r = c.request(method, path, params=params, **({"json": body} if body is not None else {}), **({"timeout": timeout} if timeout else {}))
    except httpx.HTTPError as e:
        # A deliberately short limit (search suggestions) running out is normal for a slow source, not a plugin fault.
        if not (timeout and isinstance(e, httpx.TimeoutException)):
            _note(plugin_id, _describe(e))
        raise PluginError("unreachable: {kind}", kind=e.__class__.__name__) from e
    if r.status_code == 404:
        raise PluginError("not found")
    if r.status_code not in (200, 202):
        _note(plugin_id, f"HTTP {r.status_code}")
        raise PluginError("HTTP {code}", code=r.status_code)
    try:
        data = r.json()
    except ValueError as e:
        _note(plugin_id, "not a JSON response")
        raise PluginError("not a JSON response") from e
    _note(plugin_id, "")
    return data


def _describe(e: Exception) -> str:
    """Error text for the admin area (httpx says only 'timed out' for timeouts)."""
    if isinstance(e, httpx.TimeoutException):
        return f"No answer within {TIMEOUT:.0f} s (timeout)"
    return str(e) or e.__class__.__name__


def _note(plugin_id: str, error: str) -> None:
    with db.db() as c:
        if error:
            c.execute("UPDATE plugins SET last_error = ? WHERE id = ?", (error[:200], plugin_id))
        else:
            c.execute("UPDATE plugins SET last_ok = ?, last_error = '' WHERE id = ?", (int(time.time()), plugin_id))


def fetch_manifest(url: str, token: str, plugin_id: str = "") -> dict:
    """Fetches and checks a plugin's manifest (without storing it yet)."""
    if not re.match(r"^https?://[^\s/]+", url):
        raise PluginError("The address must start with http:// or https://.")
    try:
        with _client(plugin_id or "probe", url, token) as c:
            r = c.get("/manifest")
    except httpx.HTTPError as e:
        raise PluginError("Unreachable: {kind}", kind=e.__class__.__name__) from e
    if r.status_code in (401, 403):
        raise PluginError("The plugin rejects the access token.")
    if r.status_code != 200:
        raise PluginError("This is not a plugin (HTTP {code}).", code=r.status_code)
    try:
        m = r.json()
    except ValueError as e:
        raise PluginError("This is not a plugin (not a JSON response).") from e
    return validate_manifest(m)


def add(url: str, token: str) -> dict:
    m = fetch_manifest(url, token)
    with db.db() as c:
        if c.execute("SELECT 1 FROM plugins WHERE id = ?", (m["id"],)).fetchone():
            raise PluginError("A plugin with the identifier '{id}' is already registered.", id=m["id"])
        c.execute(
            "INSERT INTO plugins(id, name, url, token, version, description, features, search_kinds, settings_schema, categories, actions, enabled, added_at, last_ok) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (m["id"], m["name"], url.rstrip("/"), token, m["version"], m["description"],
             json.dumps(m["features"]), json.dumps(m["search_kinds"]), json.dumps(m["settings"]), json.dumps(m["categories"]), json.dumps(m["actions"]), int(time.time()), int(time.time())))
    push_settings(m["id"], quiet=True)
    return get(m["id"])


def register_managed(spec: dict, source: str, rev: str) -> None:
    """Registers a managed plugin (folder in the plugins directory); the supervisor sets address and token at start."""
    with db.db() as c:
        c.execute("INSERT OR IGNORE INTO plugins(id, name, url, token, version, description, enabled, added_at, kind, dir, source, rev) "
                  "VALUES(?,?,?,?,?,?,1,?,'managed',?,?,?)",
                  (spec["id"], spec["name"], "", "", spec["version"], spec["description"], int(time.time()), spec["id"], source, rev))


def set_endpoint(plugin_id: str, url: str, token: str) -> None:
    with db.db() as c:
        c.execute("UPDATE plugins SET url = ?, token = ? WHERE id = ?", (url, token, plugin_id))


def set_meta(plugin_id: str, spec: dict, rev: str) -> None:
    with db.db() as c:
        c.execute("UPDATE plugins SET name = ?, version = ?, description = ?, rev = ? WHERE id = ?", (spec["name"], spec["version"], spec["description"], rev, plugin_id))


def set_enabled(plugin_id: str, on: bool) -> bool:
    with db.db() as c:
        return c.execute("UPDATE plugins SET enabled = ? WHERE id = ?", (1 if on else 0, plugin_id)).rowcount > 0


def refresh(plugin_id: str) -> dict | None:
    """Re-reads the manifest (version, features); on error it is recorded as the last error."""
    p = get(plugin_id)
    if not p:
        return None
    try:
        m = fetch_manifest(p["url"], _token(plugin_id), plugin_id)
        if m["id"] != plugin_id:
            raise PluginError("The plugin reports the identifier '{got}', but plugin.json and the folder say '{want}'. They must match.", got=m["id"], want=plugin_id)
    except PluginError as e:
        _note(plugin_id, str(e))
        return get(plugin_id)
    with db.db() as c:
        c.execute("UPDATE plugins SET name=?, version=?, description=?, features=?, search_kinds=?, settings_schema=?, categories=?, actions=?, last_ok=?, last_error='' WHERE id=?",
                  (m["name"], m["version"], m["description"], json.dumps(m["features"]), json.dumps(m["search_kinds"]), json.dumps(m["settings"]),
                   json.dumps(m["categories"]), json.dumps(m["actions"]), int(time.time()), plugin_id))
    push_settings(plugin_id, quiet=True)
    return get(plugin_id)


def remove(plugin_id: str) -> bool:
    with db.db() as c:
        c.execute("DELETE FROM settings WHERE key = ?", (_settings_key(plugin_id),))
        return c.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,)).rowcount > 0


# ---------- Shapes and IDs ----------

def _s(v, n: int = 300) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _https(v) -> str:
    return v if isinstance(v, str) and v.startswith("https://") and len(v) <= 500 else ""


def _pref(pid: str, local) -> str:
    """Plugin ID with prefix; empty stays empty. IDs that are too long are dropped (the UI limits them to 64 characters)."""
    if not isinstance(local, str) or not local or not re.match(r"^[A-Za-z0-9_.-]{1,40}$", local):
        return ""
    return f"{pid}:{local}"


def _track(pid: str, t: dict) -> dict | None:
    tid = _pref(pid, t.get("id"))
    if not tid or not _s(t.get("title")):
        return None
    return {"id": tid, "title": _s(t.get("title")), "artist": _s(t.get("artist")), "artist_id": _pref(pid, t.get("artist_id")),
            "album": _s(t.get("album")), "album_id": _pref(pid, t.get("album_id")), "duration": _s(t.get("duration"), 16),
            "thumb": _https(t.get("thumb")), "source": pid, **({"live": True} if t.get("live") is True else {})}  # live: a stream without an end (radio)


def _card(pid: str, it: dict) -> dict | None:
    kind = it.get("type")
    cid = _pref(pid, it.get("id"))
    if not cid:
        return None
    if kind == "artist":
        return {"type": "artist", "id": cid, "name": _s(it.get("name")), "thumb": _https(it.get("thumb")), "source": pid}
    if kind == "album":
        return {"type": "album", "id": cid, "title": _s(it.get("title")), "artist": _s(it.get("artist")), "year": _s(it.get("year"), 8),
                "kind": _s(it.get("kind"), 20) or "Album", "thumb": _https(it.get("thumb")), "source": pid}
    return None


def _items(pid: str, kind: str, raw) -> list[dict]:
    out = []
    for it in (raw if isinstance(raw, list) else [])[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        songlike = kind not in ("artists", "albums")  # songs and plugin categories (stations, episodes, ...) return tracks
        x = _track(pid, it) if songlike else _card(pid, it)
        if x:
            if songlike:
                x["type"] = "track"
            out.append(x)
    return out


# ---------- Weiterreichen ----------

def search(q: str, kind: str, only: str = "", limit: int = 25) -> list[dict]:
    """Results of all active plugins (in parallel). A failed plugin simply returns nothing."""
    plugins = [p for p in enabled_plugins("search") if (kind in p["search_kinds"] or kind in [c["id"] for c in p["categories"]]) and (not only or p["id"] == only)]
    if not plugins:
        return []

    def one(p):
        try:
            data = _call(p["id"], "/search", {"q": q, "kind": kind, "limit": limit})
            return [{**x, "source_name": p["name"]} for x in _items(p["id"], kind, data.get("items"))]
        except PluginError as e:
            log.warning("Plugin %s Suche: %s", p["id"], e)
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(plugins))) as ex:
        return [x for res in ex.map(one, plugins) for x in res]


def categories() -> list[dict]:
    """Extra search tabs offered by active plugins, labels in the current language. Plugins using the same id share one tab."""
    lang = i18n.current()
    out: dict[str, dict] = {}
    for p in enabled_plugins("search"):
        for c in p["categories"]:
            out.setdefault(c["id"], {"id": c["id"], "label": pluginproto.text(c["label"], lang)[:30]})
    return list(out.values())


_now_cache: dict[str, tuple[float, dict]] = {}
NOW_TTL = 10  # seconds a "now playing" answer is reused (many listeners of one station must not each ask the plugin)


def now_playing(ref: str) -> dict:
    """The song a live stream plays right now ({"title", "artist"}), from plugins with the 'nowplaying' feature; {} if unknown."""
    pid, local = _need(ref, "nowplaying")
    hit = _now_cache.get(ref)
    if hit and time.time() - hit[0] < NOW_TTL:
        return hit[1]
    data = _call(pid, "/nowplaying/" + local, timeout=5)
    out = {"title": _s(data.get("title"), 200), "artist": _s(data.get("artist"), 200)} if isinstance(data, dict) else {}
    if not out.get("title"):
        out = {}
    if len(_now_cache) > 500:
        _now_cache.clear()
    _now_cache[ref] = (time.time(), out)
    return out


def suggest(q: str, limit: int = 8) -> list[str]:
    """Search suggestions from plugins with the 'suggest' feature (parallel, short time limit: typing must never hang)."""
    plugins = enabled_plugins("suggest")
    if not plugins:
        return []

    def one(p):
        try:
            data = _call(p["id"], "/suggest", {"q": q}, timeout=2.5)
        except PluginError:
            return []
        raw = data.get("suggestions") if isinstance(data, dict) else None
        return [x.strip()[:100] for x in (raw if isinstance(raw, list) else [])[:limit] if isinstance(x, str) and x.strip()]

    with ThreadPoolExecutor(max_workers=min(4, len(plugins))) as ex:
        merged = [x for res in ex.map(one, plugins) for x in res]
    seen: set[str] = set()
    return [x for x in merged if not (x.lower() in seen or seen.add(x.lower()))][:limit]


def sources() -> list[dict]:
    """Sources the user sees: the own library and all active plugins (id -> display name)."""
    return [{"id": "local", "name": tr("Library")}] + [{"id": p["id"], "name": p["name"]} for p in enabled_plugins()]


def _need(ref: str, feature: str) -> tuple[str, str]:
    s = split_id(ref)
    p = get(s[0]) if s else None
    if not s or not p or not p["enabled"] or feature not in p["features"]:
        raise PluginError("Plugin not available")
    return s


def radio(ref: str, limit: int = 40) -> list[dict]:
    pid, local = _need(ref, "radio")
    data = _call(pid, "/radio/" + local, {"limit": limit})
    return [t for t in (_track(pid, x) for x in (data.get("tracks") or [])[:MAX_ITEMS] if isinstance(x, dict)) if t]


def artist_page(ref: str) -> dict:
    pid, local = _need(ref, "artist")
    d = _call(pid, "/artist/" + local)
    songs = [t for t in (_track(pid, x) for x in (d.get("songs") or [])[:MAX_ITEMS] if isinstance(x, dict)) if t]
    cards = lambda key: [c for c in (_card(pid, {**x, "type": "album"}) for x in (d.get(key) or [])[:MAX_ITEMS] if isinstance(x, dict)) if c]  # noqa: E731
    return {"id": _pref(pid, d.get("id")) or ref, "name": _s(d.get("name")), "description": _s(d.get("description"), 2000),
            "subscribers": _s(d.get("subscribers"), 60), "thumb": _https(d.get("thumb")), "songs": songs, "songs_browse_id": "",
            "albums": cards("albums"), "singles": cards("singles")}


def album_page(ref: str) -> dict:
    pid, local = _need(ref, "album")
    d = _call(pid, "/album/" + local)
    tracks = [t for t in (_track(pid, x) for x in (d.get("tracks") or [])[:MAX_ITEMS * 3] if isinstance(x, dict)) if t]
    return {"id": _pref(pid, d.get("id")) or ref, "title": _s(d.get("title")), "kind": _s(d.get("kind"), 20) or "Album",
            "year": _s(d.get("year"), 8), "duration": _s(d.get("duration"), 40), "thumb": _https(d.get("thumb")),
            "artist": _s(d.get("artist")), "artist_id": _pref(pid, d.get("artist_id")), "tracks": tracks}


_PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified")


def open_stream(ref: str, range_header: str = ""):
    """Opens the plugin's audio stream. -> (status code, headers, byte iterator with close()); PluginError if nothing comes."""
    pid, local = _need(ref, "stream")
    p = get(pid)
    client = _client(pid, p["url"], _token(pid))
    headers = {"Range": range_header} if re.match(r"^bytes=\d*-\d*$", range_header or "") else {}
    try:
        resp = client.send(client.build_request("GET", "/stream/" + local, headers=headers), stream=True)
    except httpx.HTTPError as e:
        client.close()
        _note(pid, _describe(e))
        raise PluginError("unreachable") from e
    if resp.status_code not in (200, 206) or not resp.headers.get("content-type", "").startswith("audio/"):
        code = resp.status_code
        resp.close()
        client.close()
        raise PluginError("not found") if code == 404 else PluginError("HTTP {code}", code=code)

    def body():
        try:
            yield from (iter([resp.content]) if resp.is_stream_consumed else resp.iter_raw(64 * 1024))  # fertig gelesene Antworten (Tests)
        finally:
            resp.close()
            client.close()

    return resp.status_code, {k: v for k, v in resp.headers.items() if k.lower() in _PASS_HEADERS}, body()


# ---------- Plugin settings ----------
# A plugin describes its settings in the manifest (`settings`); the admin edits them in the admin area. The server keeps the
# values and pushes them to the plugin (PUT /settings) at start, after every change and whenever the manifest is read again.

def _settings_key(plugin_id: str) -> str:
    return f"plugin_settings:{plugin_id}"


def settings_values(p: dict) -> dict:
    """Saved values of a plugin, complete (defaults for everything not set) and valid."""
    try:
        saved = json.loads(db.get_setting(_settings_key(p["id"]), "{}"))
    except ValueError:
        saved = {}
    return pluginproto.clean_values(p["settings"], saved)


def _info(data) -> list[dict]:
    """Read-only facts a plugin may show next to its settings (version of a tool, last update, ...), in the current language."""
    lang = i18n.current()
    raw = data.get("info") if isinstance(data, dict) else None
    out = []
    for it in (raw if isinstance(raw, list) else [])[:12]:
        if isinstance(it, dict) and it.get("label") and isinstance(it.get("value"), (str, int, float, dict)):
            value = pluginproto.text(it["value"], lang) if isinstance(it["value"], dict) else str(it["value"])  # a text, or one text per language
            out.append({"label": pluginproto.text(it["label"], lang)[:60], "value": value[:200]})
    return out


def push_settings(plugin_id: str, quiet: bool = False) -> list[dict]:
    """Sends the saved values to the plugin; returns its read-only info. quiet: failures are ignored (e.g. the plugin is still starting)."""
    p = get(plugin_id)
    if not p or not p["settings"]:
        return []
    try:
        return _info(_call(plugin_id, "/settings", method="PUT", body={"values": settings_values(p)}, timeout=5))
    except PluginError:
        if quiet:
            return []
        raise


def _field_view(e: dict, lang: str) -> dict:
    """One settings/action field for the admin area: texts in the current language."""
    item = {k: e[k] for k in ("key", "type", "default", "min", "max", "depends_on", "max_length", "required", "secret") if k in e}
    item.update(label=pluginproto.text(e["label"], lang), help=pluginproto.text(e["help"], lang))
    if e.get("unit"):
        item["unit"] = pluginproto.text(e["unit"], lang)
    if e.get("placeholder"):
        item["placeholder"] = pluginproto.text(e["placeholder"], lang)
    if e["type"] == "choice":
        item["choices"] = [{"value": c["value"], "label": pluginproto.text(c["label"], lang)} for c in e["choices"]]
    return item


def settings_view(plugin_id: str) -> dict:
    """What the admin area shows: settings schema, values, the plugin's read-only info and its actions (texts in the current language)."""
    p = get(plugin_id)
    if not p or not (p["settings"] or p["actions"]):
        raise PluginError("This plugin has no settings.")
    lang = i18n.current()
    schema = [_field_view(e, lang) for e in p["settings"]]
    actions = [{"id": a["id"], "label": pluginproto.text(a["label"], lang), "help": pluginproto.text(a["help"], lang),
                "fields": [_field_view(f, lang) for f in a["fields"]]} for a in p["actions"]]
    try:
        info = _info(_call(plugin_id, "/settings")) if p["settings"] else []
    except PluginError:
        info = []
    values = settings_values(p)
    for item in schema:
        if item.get("secret"):  # a saved password or token is never handed out again; the sheet only learns that there is one
            item["has_value"] = bool(values.get(item["key"]))
            values[item["key"]] = ""
            item["default"] = ""
    return {"schema": schema, "values": values, "info": info, "actions": actions}


def run_action(plugin_id: str, action_id: str, values) -> dict:
    """Runs one of the plugin's actions with the filled-in form (validated against the action's fields). -> {"ok": bool, "message": str}"""
    p = get(plugin_id)
    action = next((a for a in (p["actions"] if p else []) if a["id"] == action_id), None)
    if not action:
        raise PluginError("This plugin has no such action.")
    clean = pluginproto.check_values(action["fields"], values)
    data = _call(plugin_id, f"/actions/{action_id}", method="POST", body={"values": clean}, timeout=25)
    log.info("Action %s of plugin %s run", action_id, plugin_id)
    if not isinstance(data, dict):
        return {"ok": False, "message": ""}
    return {"ok": data.get("ok") is True, "message": pluginproto.text(data.get("message") or "", i18n.current())[:400]}  # a text, or one text per language


def save_settings(plugin_id: str, values) -> dict:
    """Validates and saves new values, applies them in the plugin at once."""
    p = get(plugin_id)
    if not p or not p["settings"]:
        raise PluginError("This plugin has no settings.")
    values = dict(values) if isinstance(values, dict) else values
    if isinstance(values, dict):
        saved = settings_values(p)
        for e in p["settings"]:
            if e.get("secret") and not values.get(e["key"]):  # left empty: keep the saved secret
                values[e["key"]] = saved.get(e["key"], "")
    clean = pluginproto.check_values(p["settings"], values)
    db.set_setting(_settings_key(plugin_id), json.dumps(clean))
    log.info("Settings of plugin %s changed", plugin_id)
    push_settings(plugin_id)
    return settings_view(plugin_id)


# ---------- Hint about upcoming tracks ----------

MAX_PREFETCH_IDS = 20


def prefetch(refs: list[str]) -> int:
    """Tells plugins with the 'prefetch' feature which of their tracks are queued next, so they can fetch them ahead of time.

    The plugin decides how many of them it takes (its own setting). Best effort: short time limit, failures are ignored.
    Returns how many tracks were passed on."""
    by_plugin: dict[str, list[str]] = {}
    for ref in refs[:MAX_PREFETCH_IDS]:
        s = split_id(ref)
        p = get(s[0]) if s else None
        if s and p and p["enabled"] and "prefetch" in p["features"]:
            by_plugin.setdefault(s[0], []).append(s[1])
    sent = 0
    for pid, ids in by_plugin.items():
        try:
            _call(pid, "/prefetch", method="POST", body={"ids": ids}, timeout=3)
            sent += len(ids)
        except PluginError:
            log.debug("Plugin %s did not take the prefetch hint", pid)
    return sent
