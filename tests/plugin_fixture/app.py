"""Minimal plugin for the plugin host tests (speaks only as much of the protocol as the tests need)."""
import json
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI()
SCHEMA = [
    {"key": "ahead", "type": "int", "label": {"en": "Songs ahead", "de": "Titel im Voraus"}, "min": 0, "max": 10, "default": 2},
    {"key": "keep", "type": "bool", "label": "Keep songs", "help": {"en": "Keep them", "de": "Behalten"}, "default": False},
    {"key": "mode", "type": "choice", "label": "Mode", "default": "a", "depends_on": "keep",
     "choices": [{"value": "a", "label": "Mode A"}, {"value": "b", "label": {"en": "Mode B", "de": "Modus B"}}]},
]
VALUES = json.loads(os.environ.get("PLUGIN_SETTINGS", "{}"))
HINTS: list = []


def auth(request: Request):
    if request.headers.get("authorization") != "Bearer " + os.environ["PLUGIN_TOKEN"]:
        raise HTTPException(401)


@app.get("/manifest", dependencies=[Depends(auth)])
def manifest():
    return {"id": "demo", "name": "Demo-Plugin", "version": "0.0.1", "description": "Test plugin", "api_version": 1, "features": ["search", "stream", "prefetch"],
            "search_kinds": ["songs"], "settings": SCHEMA}


@app.get("/search", dependencies=[Depends(auth)])
def search(q: str = "", kind: str = "songs", limit: int = 25):
    return {"items": [{"id": "d1", "title": "Demo-Lied", "artist": "Demo", "artist_id": "", "album": "", "album_id": "", "duration": "1:00", "thumb": ""}]}


@app.get("/stream/{tid}", dependencies=[Depends(auth)])
def stream(tid: str):
    if tid != "d1":
        raise HTTPException(404)
    return Response(b"AUDIO" * 100, media_type="audio/mpeg")


@app.get("/env", dependencies=[Depends(auth)])
def env():
    return {"keys": sorted(os.environ), "data_dir": os.environ.get("PLUGIN_DATA_DIR", ""), "cwd": os.getcwd()}


@app.get("/settings", dependencies=[Depends(auth)])
def get_settings():
    return {"info": [{"label": {"en": "Received", "de": "Erhalten"}, "value": json.dumps(VALUES, sort_keys=True)}]}


@app.put("/settings", dependencies=[Depends(auth)])
async def put_settings(request: Request):
    VALUES.clear()
    VALUES.update((await request.json())["values"])
    return get_settings()


@app.post("/prefetch", status_code=202, dependencies=[Depends(auth)])
async def prefetch(request: Request):
    HINTS.extend((await request.json())["ids"])
    return {"accepted": len(HINTS)}


@app.get("/hints", dependencies=[Depends(auth)])
def hints():
    return {"ids": HINTS}
