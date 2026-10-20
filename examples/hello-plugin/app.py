"""Hello plugin: the smallest complete template for a NxtSound plugin (protocol version 1, see PLUGINS.md in the server repo).

The server starts this file as its own process (`uvicorn app:app`) and talks to it over HTTP. You only have to bring the
functions below to life: where a fixed list stands here, you later query your service or read files.

All answers are JSON in the shapes from PLUGINS.md; IDs are YOUR OWN (`t1`, `ar1`, ...). The server prefixes them with
`hello:`; you never see the prefix.
"""
import hmac
import json
import math
import os
import struct
import wave
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

# ---------- Settings from the server ----------
# PLUGIN_TOKEN: access token, every request from the server carries it. PLUGIN_DATA_DIR: your own writable folder.
TOKEN = os.getenv("PLUGIN_TOKEN", "")
DATA = Path(os.getenv("PLUGIN_DATA_DIR", Path(__file__).parent / "data"))

# plugin.json is the single source for who the plugin is (id, name, version, description) and which settings it has.
# The manifest below is built from it, so renaming the plugin means editing that one file.
SPEC = json.loads((Path(__file__).parent / "plugin.json").read_text(encoding="utf-8"))
# The admin's settings: the server passes them at start (PLUGIN_SETTINGS) and again after every change (PUT /settings).
SETTINGS = {e["key"]: e["default"] for e in SPEC["settings"]}
SETTINGS.update(json.loads(os.getenv("PLUGIN_SETTINGS", "{}")))

# ---------- Your "source": a fixed list here ----------
ARTIST = {"id": "ar1", "name": "Hello Band"}
ALBUM = {"id": "al1", "title": "First Tones", "year": "2026"}
TRACKS = {
    "t1": {"title": "Tone A", "hz": 440, "duration": "0:03"},
    "t2": {"title": "Tone C", "hz": 523, "duration": "0:03"},
    "t3": {"title": "Tone E", "hz": 659, "duration": "0:03"},
}


def track(tid: str) -> dict:
    """A track in the shape of the protocol (id and title are required, the rest may be empty)."""
    t = TRACKS[tid]
    title = t["title"].upper() if SETTINGS["shout"] else t["title"]  # the example setting "Titles in capital letters"
    return {"id": tid, "title": title, "artist": ARTIST["name"], "artist_id": ARTIST["id"], "album": ALBUM["title"],
            "album_id": ALBUM["id"], "duration": t["duration"], "thumb": ""}  # thumb: https address of a cover, or empty


def album_card() -> dict:
    return {"type": "album", "id": ALBUM["id"], "title": ALBUM["title"], "artist": ARTIST["name"], "year": ALBUM["year"], "kind": "Album", "thumb": ""}


def audio_file(tid: str) -> Path:
    """Generates (once) a small WAV file with the track's tone. A real plugin would fetch the file from its source here."""
    path = DATA / f"{tid}.wav"
    if not path.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        rate, hz = 22050, TRACKS[tid]["hz"]
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate))) for i in range(rate * 3)))
    return path


# ---------- Protection: only the server may ask ----------

def auth(request: Request) -> None:
    given = request.headers.get("authorization", "")
    if not TOKEN or not hmac.compare_digest(given.encode(), f"Bearer {TOKEN}".encode()):
        raise HTTPException(401, "Token missing or wrong")


app = FastAPI(title="Hello-Plugin", docs_url=None, redoc_url=None, openapi_url=None)
router = APIRouter(dependencies=[Depends(auth)])


# ---------- Protocol ----------

@router.get("/manifest")
def manifest():
    """Who am I, what can I do? 'features' lists the functions the server may call."""
    return {"id": SPEC["id"], "name": SPEC["name"], "version": SPEC["version"], "api_version": 1, "description": SPEC["description"],
            "features": ["search", "stream", "radio", "artist", "album", "suggest"], "search_kinds": ["songs", "artists", "albums"],
            "settings": SPEC["settings"]}


@router.put("/settings")
async def put_settings(request: Request):
    """The admin changed the settings: the complete set arrives as {"values": {...}}. Apply it at once, no restart needed."""
    SETTINGS.update((await request.json())["values"])
    return get_settings()


@router.get("/settings")
def get_settings():
    """Optional read-only facts the admin area shows below the settings form."""
    return {"info": [{"label": {"en": "Songs offered", "de": "Angebotene Titel"}, "value": len(TRACKS)}]}


@router.get("/search")
def search(q: str = Query(min_length=1), kind: str = "songs", limit: int = Query(25, ge=1, le=50)):
    """Results for a search term. kind = songs | artists | albums (according to 'search_kinds' in the manifest)."""
    term = q.lower()
    if kind == "songs":
        hits = [track(t) for t, d in TRACKS.items() if term in d["title"].lower() or term in ARTIST["name"].lower() or term in ALBUM["title"].lower()]
        hits = hits[:SETTINGS["max_results"]]
    elif kind == "artists":
        hits = [{"type": "artist", "id": ARTIST["id"], "name": ARTIST["name"], "thumb": ""}] if term in ARTIST["name"].lower() else []
    else:
        hits = [album_card()] if term in ALBUM["title"].lower() or term in ARTIST["name"].lower() else []
    return {"items": hits[:limit]}


@router.get("/suggest")
def suggest(q: str = Query(min_length=1)):
    """Suggestions while the user types (optional, feature 'suggest'). Must answer quickly."""
    names = [d["title"] for d in TRACKS.values()] + [ARTIST["name"], ALBUM["title"]]
    return {"suggestions": [n for n in names if n.lower().startswith(q.lower())][:8]}


@router.get("/radio/{tid}")
def radio(tid: str, limit: int = Query(40, ge=1, le=100)):
    """Similar tracks for a track (feature 'radio')."""
    if tid not in TRACKS:
        raise HTTPException(404, "unknown ID")
    return {"tracks": [track(t) for t in TRACKS if t != tid][:limit]}


@router.get("/artist/{aid}")
def artist(aid: str):
    """Artist page (feature 'artist')."""
    if aid != ARTIST["id"]:
        raise HTTPException(404, "unknown ID")
    return {"id": aid, "name": ARTIST["name"], "description": "A made-up band for developers.", "subscribers": "", "thumb": "",
            "songs": [track(t) for t in TRACKS], "albums": [album_card()], "singles": []}


@router.get("/album/{alid}")
def album(alid: str):
    """Album page with track list (feature 'album')."""
    if alid != ALBUM["id"]:
        raise HTTPException(404, "unknown ID")
    return {"id": alid, "title": ALBUM["title"], "kind": "Album", "year": ALBUM["year"], "duration": "0:09", "thumb": "",
            "artist": ARTIST["name"], "artist_id": ARTIST["id"], "tracks": [track(t) for t in TRACKS]}


@router.get("/stream/{tid}")
def stream(tid: str):
    """The audio file. FileResponse answers Range requests (seeking, iOS) by itself; Content-Type must be audio/*."""
    if tid not in TRACKS:
        raise HTTPException(404, "unknown ID")
    return FileResponse(audio_file(tid), media_type="audio/wav")


app.include_router(router)
