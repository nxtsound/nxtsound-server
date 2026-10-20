import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="server-test-")
os.environ["DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["MUSIC_DIR"] = str(Path(_tmp) / "music")
os.environ["COVER_DIR"] = str(Path(_tmp) / "covers")
os.environ["SCAN_ON_START"] = "0"
os.environ["PLUGINS_DIR"] = str(Path(_tmp) / "plugins")
os.environ["PLUGIN_DATA_DIR"] = str(Path(_tmp) / "plugin-data")
os.environ["PLUGIN_USE_VENV"] = "0"  # plugins run with the tests' Python (no pip, no network)
os.environ["PLUGINS_ON_START"] = "0"
os.environ["ADMIN_PASSWORD_SECRET_FOR_TEST"] = "must-not-reach-the-plugin"
os.environ["SYSMON_ON_START"] = "0"  # no background checks in the tests
os.environ["UPDATE_REPO"] = ""  # tests that look for updates set the repository themselves (and use a fake transport)
os.environ["ANALYZE"] = "0"  # no background analysis in the tests
os.environ["ENRICH"] = "0"  # no network in the tests
os.environ["APP_SCRYPT_N"] = "1024"  # fast hashes in the tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


def vid(n: int) -> str:
    return f"test:{n:08d}"  # valid track ID (<source>:<id>), does not have to be in the library


def make_song(n: int, artist="Artist A", artist_id="aaaaaaaaaaaaaaa"):
    """Track data as the web UI sends it to the API (playlists, likes, history)."""
    return {"id": vid(n), "title": f"Song {n}", "artist": artist, "artist_id": artist_id, "album": "Album X",
            "album_id": "bbbbbbbbbbbbbbb", "duration": "3:30", "thumb": ""}


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts with an empty database (foreign keys delete everything with it)."""
    with main.db.db() as c:
        c.execute("DELETE FROM users")
        for table in ("lib_tracks", "bpm", "lyrics", "stream_stats", "settings", "meta_artist", "meta_album", "plugins", "notifications", "follows"):
            c.execute(f"DELETE FROM {table}")
    main._login_fails.clear()
    main._home_cache.clear()
    yield


def write_wav(path: Path, seconds: float = 1.0, title="", artist="", album="", albumartist="", track="", genre="", date="", cover: bytes | None = None):
    """Writes a small WAV file with ID3 tags (and optionally a cover)."""
    import wave
    from mutagen.id3 import APIC
    from mutagen.wave import WAVE
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))
    f = WAVE(str(path))
    f.add_tags()
    from mutagen.id3 import TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
    for frame, val in ((TIT2, title), (TPE1, artist), (TALB, album), (TPE2, albumartist), (TRCK, track), (TCON, genre), (TDRC, date)):
        if val:
            f.tags.add(frame(encoding=3, text=val))
    if cover:
        f.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=cover))
    f.save()


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """Small library: two artists, three albums, one track without tags. Scanned."""
    music = tmp_path / "music"
    monkeypatch.setattr(main.library, "MUSIC_DIR", music)
    monkeypatch.setattr(main.library, "IMPORT_DIR", tmp_path / "imported")
    monkeypatch.setattr(main.library, "COVER_DIR", tmp_path / "covers")
    write_wav(music / "a" / "x" / "01.wav", title="Alpha", artist="Artist A", album="Album X", track="1/3", genre="Rock", date="2020", cover=PNG)
    write_wav(music / "a" / "x" / "02.wav", title="Beta", artist="Artist A", album="Album X", track="2/3", genre="Rock", date="2020")
    write_wav(music / "a" / "x" / "03.wav", title="Gamma", artist="Artist A", album="Album X", track="3/3", genre="Rock", date="2020")
    write_wav(music / "a" / "y" / "01.wav", title="Delta Single", artist="Artist A", album="Single Y", track="1", genre="Pop", date="2022")
    write_wav(music / "b" / "z" / "01.wav", title="Epsilon", artist="Artist B", album="Album Z", genre="Rock", date="2021")
    write_wav(music / "loose" / "no tags.wav")
    main.library.scan(full=True)
    return music


def ids_by_title(client) -> dict:
    """Title -> track ID via search (all titles of the library)."""
    out = {}
    for q in ("a", "e", "o", "i", "u"):
        for t in client.get("/api/search", params={"q": q}).json()["items"]:
            out[t["title"]] = t["id"]
    return out


class Client(TestClient):
    """TestClient that sends the session's CSRF token automatically (like the frontend)."""

    csrf = ""

    def request(self, method, url, **kw):
        if self.csrf and method.upper() not in ("GET", "HEAD", "OPTIONS"):
            kw["headers"] = {"X-CSRF-Token": self.csrf, **(kw.get("headers") or {})}
        r = super().request(method, url, **kw)
        if str(url).split("?")[0] in ("/api/login", "/api/setup", "/api/me") and r.status_code == 200:
            self.csrf = r.json().get("csrf", self.csrf)
        return r


@pytest.fixture()
def anon():
    """Ohne Login."""
    return Client(main.app)


PW = "secret-1234"


@pytest.fixture()
def client():
    """Logged in as 'Tester' (the first user is admin)."""
    return make_client("Tester")


def make_client(name: str) -> TestClient:
    """Creates the user (the very first becomes admin) and logs them in."""
    if main.db.find_user(name) is None:
        main.db.create_user(name, PW)
    c = Client(main.app)
    r = c.post("/api/login", json={"name": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def alice():
    return make_client("Alice")


@pytest.fixture()
def bob():
    return make_client("Bob")
