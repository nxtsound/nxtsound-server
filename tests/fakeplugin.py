"""Stand-in for a plugin following the NxtSound plugin protocol (httpx.MockTransport, no network).

Serves as a runnable reference: the core's tests and plugincheck.py run against it.
"""
import json

import httpx

TOKEN = "secret"
AUDIO = bytes(range(256)) * 8  # 2048 Bytes "Audio"

TRACKS = {
    "t1": {"id": "t1", "title": "First Song", "artist": "Fake Band", "artist_id": "ar1", "album": "Fake Album", "album_id": "al1",
           "duration": "3:10", "thumb": "https://img.example/t1.jpg"},
    "t2": {"id": "t2", "title": "Second Song", "artist": "Fake Band", "artist_id": "ar1", "album": "Fake Album", "album_id": "al1",
           "duration": "2:45", "thumb": ""},
}


def make(manifest_overrides: dict | None = None, token: str = TOKEN, broken: str = "") -> httpx.MockTransport:
    calls: list = []  # (method, path, json body) of the settings and prefetch calls, for the tests
    values: dict = {}
    manifest = {"id": "fake", "name": "Fake Source", "version": "1.2.3", "api_version": 1, "description": "Test",
                "features": ["search", "stream", "radio", "artist", "album", "suggest"], "search_kinds": ["songs", "artists", "albums"],
                **(manifest_overrides or {})}

    def json_response(data, status=200):
        return httpx.Response(status, json=data)

    def handler(req: httpx.Request) -> httpx.Response:
        if token and req.headers.get("authorization") != "Bearer " + token:
            return json_response({"detail": "Token"}, 401)
        path, q = req.url.path, dict(req.url.params)
        if path == "/manifest":
            return json_response(manifest)
        if path == "/search":
            if broken == "search":
                return json_response({"items": [{"id": "invalid id!", "title": ""}]})
            term = q.get("q", "").lower()
            if q.get("kind") == "radio":  # a plugin category: track-shaped results
                items = [{"id": "st1", "title": "Radio One", "artist": "Germany", "thumb": "", "live": True}]
                if broken == "live":  # a flag that is not a boolean
                    items.append({"id": "st2", "title": "Not Live", "artist": "X", "thumb": "", "live": "yes"})
                return json_response({"items": items})
            if q.get("kind") == "songs":
                return json_response({"items": [t for t in TRACKS.values() if term in t["title"].lower() or term in t["artist"].lower() or term == "a"]})
            if q.get("kind") == "artists":
                return json_response({"items": [{"type": "artist", "id": "ar1", "name": "Fake Band", "thumb": ""}]})
            return json_response({"items": [{"type": "album", "id": "al1", "title": "Fake Album", "artist": "Fake Band", "year": "2020", "thumb": ""}]})
        if path == "/settings":
            if req.method == "PUT":
                body = json.loads(req.content)
                calls.append(("PUT", path, body))
                values.clear()
                values.update(body["values"])
            return json_response({"info": [{"label": "Seen", "value": json.dumps(values, sort_keys=True)},
                                           {"label": {"en": "State", "de": "Zustand"}, "value": {"en": "fine", "de": "gut"}}]})
        if path.startswith("/nowplaying/"):
            calls.append(("GET", path, None))
            return json_response({"title": "Fake Song", "artist": "Fake Artist"} if path.endswith("/st1") else {"title": "", "artist": ""})
        if path.startswith("/actions/"):
            body = json.loads(req.content)
            calls.append(("POST", path, body))
            if body["values"].get("name") == "refuse":
                return json_response({"ok": False, "message": "the source said no"})
            if body["values"].get("name") == "german":
                return json_response({"ok": True, "message": {"en": "added", "de": "hinzugefügt"}})
            return json_response({"ok": True, "message": "added " + body["values"].get("name", "")})
        if path == "/prefetch":
            calls.append(("POST", path, json.loads(req.content)))
            return json_response({"accepted": len(calls)}, 202)
        if path == "/suggest":
            return json_response({"suggestions": [q.get("q", "") + " live", q.get("q", "") + " remix"]})
        if path.startswith("/radio/"):
            t = TRACKS.get(path.split("/")[-1])
            return json_response({"tracks": [x for x in TRACKS.values() if x is not t]}) if t else json_response({}, 404)
        if path == "/artist/ar1":
            return json_response({"id": "ar1", "name": "Fake Band", "description": "A test band", "songs": list(TRACKS.values()),
                                  "albums": [{"id": "al1", "title": "Fake Album", "artist": "Fake Band", "year": "2020"}], "singles": []})
        if path == "/album/al1":
            return json_response({"id": "al1", "title": "Fake Album", "year": "2020", "artist": "Fake Band", "artist_id": "ar1", "tracks": list(TRACKS.values())})
        if path.startswith("/stream/"):
            if path.split("/")[-1] not in (*TRACKS, "st1"):
                return json_response({}, 404)
            rng = req.headers.get("range", "")
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start, end = int(a or 0), int(b) if b else len(AUDIO) - 1
                part = AUDIO[start:end + 1]
                return httpx.Response(206, content=part, headers={"content-type": "audio/mpeg", "accept-ranges": "bytes",
                                                                 "content-range": f"bytes {start}-{start + len(part) - 1}/{len(AUDIO)}"})
            return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg", "accept-ranges": "bytes"})
        return json_response({}, 404)

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport
