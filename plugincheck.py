"""Checks a running plugin service against the NxtSound plugin protocol (see PLUGINS.md).

    python plugincheck.py http://localhost:8100 --token SECRET --query "radiohead"

Prints all deviations and exits with code 1 if there are any. The core's tests use the same check
and so do the plugins' tests (as a copy).
"""
import argparse
import re
import sys

import httpx

import pluginproto

ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def _shape(kind: str, it, where: str, out: list[str]) -> None:
    if not isinstance(it, dict):
        out.append(f"{where}: entry is not an object")
        return
    if not isinstance(it.get("id"), str) or not ID.match(it["id"]):
        out.append(f"{where}: 'id' is missing or invalid (A-Z a-z 0-9 _ . -, at most 40 characters): {it.get('id')!r}")
    label = "title" if kind in ("songs", "albums") else "name"
    if not isinstance(it.get(label), str) or not it[label].strip():
        out.append(f"{where}: '{label}' is missing")
    if kind in ("artists", "albums") and it.get("type") != kind[:-1]:
        out.append(f"{where}: 'type' must be '{kind[:-1]}'")
    if "live" in it and not isinstance(it["live"], bool):
        out.append(f"{where}: 'live' must be true or false")
    th = it.get("thumb", "")
    if th and not (isinstance(th, str) and th.startswith("https://")):
        out.append(f"{where}: 'thumb' must be an https address or empty")
    for ref in ("artist_id", "album_id"):
        if it.get(ref) and not (isinstance(it[ref], str) and ID.match(it[ref])):
            out.append(f"{where}: '{ref}' is invalid: {it[ref]!r}")


def check(base_url: str, token: str = "", query: str = "a", transport=None) -> list[str]:
    """List of deviations (empty = the plugin speaks the protocol)."""
    out: list[str] = []
    hdr = {"X-NxtSound-Api": str(pluginproto.API_VERSION)}
    kw = {"transport": transport} if transport else {}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=20, headers={**hdr, **({"Authorization": "Bearer " + token} if token else {})}, **kw) as c, \
            httpx.Client(base_url=base_url.rstrip("/"), timeout=20, headers=hdr, **kw) as anon:

        def get(path, label, **params):
            try:
                return c.get(path, params=params or None)
            except httpx.HTTPError as e:
                out.append(f"{label}: unreachable ({e.__class__.__name__})")
                return None

        r = get("/manifest", "GET /manifest")
        if r is None or r.status_code != 200:
            out.append(f"GET /manifest: expected 200, got {r.status_code if r is not None else 'nothing'}")
            return out
        try:
            m = pluginproto.validate_manifest(r.json())
        except (ValueError, pluginproto.PluginError) as e:
            return out + [f"Manifest: {e}"]
        if token:
            ar = anon.get("/manifest")
            if ar.status_code not in (401, 403):
                out.append(f"Without a token the plugin must answer 401 or 403, got {ar.status_code}")

        song_id = artist_id = album_id = ""
        if "search" in m["features"]:
            for kind in m["search_kinds"] + [c["id"] for c in m["categories"]]:
                songlike = kind not in ("artists", "albums")  # songs and categories return tracks
                r = get("/search", f"GET /search ({kind})", q=query, kind=kind, limit=10)
                if r is None or r.status_code != 200:
                    out.append(f"GET /search kind={kind}: expected 200, got {r.status_code if r is not None else 'nothing'}")
                    continue
                items = r.json().get("items") if isinstance(r.json(), dict) else None
                if not isinstance(items, list):
                    out.append(f"GET /search kind={kind}: 'items' must be a list")
                    continue
                for i, it in enumerate(items[:10]):
                    _shape("songs" if songlike else kind, it, f"search/{kind}[{i}]", out)
                if songlike and not song_id and items and isinstance(items[0], dict):
                    song_id = items[0].get("id", "")
                    artist_id = items[0].get("artist_id", "") or artist_id
                    album_id = items[0].get("album_id", "") or album_id
                if kind == "artists" and items and isinstance(items[0], dict):
                    artist_id = items[0].get("id", "") or artist_id
                if kind == "albums" and items and isinstance(items[0], dict):
                    album_id = items[0].get("id", "") or album_id
            if not song_id and ("songs" in m["search_kinds"] or m["categories"]):
                out.append(f"The search for {query!r} returns no tracks; further checks skipped (try another --query).")

        if "radio" in m["features"] and song_id:
            r = get("/radio/" + song_id, "GET /radio", limit=5)
            if r is None or r.status_code != 200 or not isinstance(r.json().get("tracks"), list):
                out.append("GET /radio/{id}: expected 200 with 'tracks' as a list")
            else:
                for i, it in enumerate(r.json()["tracks"][:5]):
                    _shape("songs", it, f"radio[{i}]", out)
        if "artist" in m["features"] and artist_id:
            r = get("/artist/" + artist_id, "GET /artist")
            if r is None or r.status_code != 200 or not isinstance(r.json().get("name"), str) or not isinstance(r.json().get("songs"), list):
                out.append("GET /artist/{id}: expected 200 with 'name' and 'songs' (list)")
        if "album" in m["features"] and album_id:
            r = get("/album/" + album_id, "GET /album")
            if r is None or r.status_code != 200 or not isinstance(r.json().get("title"), str) or not isinstance(r.json().get("tracks"), list):
                out.append("GET /album/{id}: expected 200 with 'title' and 'tracks' (list)")
        if "suggest" in m["features"]:
            r = get("/suggest", "GET /suggest", q=query[:3] or "a")
            if r is None or r.status_code != 200 or not isinstance(r.json().get("suggestions"), list) \
                    or not all(isinstance(x, str) for x in r.json()["suggestions"]):
                out.append("GET /suggest: expected 200 with 'suggestions' as a list of strings")
        if m["settings"]:  # the plugin has settings: it must report info and accept the defaults
            r = get("/settings", "GET /settings")
            if r is None or r.status_code != 200 or not isinstance(r.json(), dict):
                out.append("GET /settings: expected 200 with an object (optionally {'info': [{label, value}]})")
            defaults = {e["key"]: e["default"] for e in m["settings"]}
            try:
                r = c.put("/settings", json={"values": defaults})
                if r.status_code != 200:
                    out.append(f"PUT /settings: expected 200, got {r.status_code}")
            except httpx.HTTPError as e:
                out.append(f"PUT /settings: unreachable ({e.__class__.__name__})")
        if "prefetch" in m["features"]:
            try:
                r = c.post("/prefetch", json={"ids": []})
                if r.status_code not in (200, 202):
                    out.append(f"POST /prefetch: expected 200 or 202, got {r.status_code}")
            except httpx.HTTPError as e:
                out.append(f"POST /prefetch: unreachable ({e.__class__.__name__})")
        if "nowplaying" in m["features"] and song_id:
            r = get("/nowplaying/" + song_id, "GET /nowplaying")
            if r is None or r.status_code != 200 or not isinstance(r.json(), dict) or not all(isinstance(v, str) for v in r.json().values()):
                out.append("GET /nowplaying/{id}: expected 200 with an object of texts ({'title', 'artist'}, empty if unknown)")
        if "stream" in m["features"]:
            if song_id:
                try:  # only the first bytes are read: a live stream never ends
                    with c.stream("GET", "/stream/" + song_id, headers={"Range": "bytes=0-99"}) as r:
                        first = next(r.iter_bytes(), b"") if r.status_code in (200, 206) else b""
                        code, ctype, crange = r.status_code, r.headers.get("content-type", ""), r.headers.get("content-range")
                except httpx.HTTPError as e:
                    out.append(f"GET /stream/{{id}}: unreachable ({e.__class__.__name__})")
                    code = 0
                if not code:
                    pass
                elif code not in (200, 206):
                    out.append(f"GET /stream/{{id}} with Range: expected 200 or 206, got {code}")
                elif not ctype.startswith("audio/"):
                    out.append(f"GET /stream/{{id}}: Content-Type must be audio/*, is {ctype!r}")
                elif not first:
                    out.append("GET /stream/{id}: empty response")
                elif code == 206 and not crange:
                    out.append("GET /stream/{id}: 206 needs a Content-Range header")
            if c.get("/stream/does-not-exist-xyz").status_code != 404:
                out.append("GET /stream/{unknown id}: expected 404")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--token", default="")
    ap.add_argument("--query", default="a", help="search term the plugin returns results for")
    a = ap.parse_args()
    problems = check(a.url, a.token, a.query)
    for p in problems:
        print("✗", p)
    print("✓ The plugin speaks protocol v%d." % pluginproto.API_VERSION if not problems else f"{len(problems)} deviation(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
