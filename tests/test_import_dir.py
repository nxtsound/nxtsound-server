"""The import folder: a second, writable library folder where plugins keep songs; scanned together with the music folder."""
import time

import library
from conftest import write_wav


def titles(client):
    return {t["title"]: t["id"] for t in client.get("/api/search", params={"q": "a"}).json()["items"]}


def test_files_in_the_import_folder_join_the_library(alice, lib):
    write_wav(library.IMPORT_DIR / "New Artist" / "New Album" / "Fresh Song.wav")
    write_wav(library.IMPORT_DIR / "Solo Artist - Plain Song.wav")
    library.scan()
    rows = {t["title"]: t for t in alice.get("/api/search", params={"q": "song"}).json()["items"]}
    assert rows["Fresh Song"]["artist"] == "New Artist" and rows["Fresh Song"]["album"] == "New Album"  # the prefix is not read as a folder
    assert rows["Plain Song"]["artist"] == "Solo Artist"
    assert alice.get("/api/stream/" + rows["Fresh Song"]["id"]).status_code == 200
    assert alice.get("/api/stream/" + rows["Fresh Song"]["id"]).content[:4] == b"RIFF"


def test_same_relative_path_in_both_folders_are_two_tracks(alice, lib):
    write_wav(lib / "Dup - Same.wav")
    write_wav(library.IMPORT_DIR / "Dup - Same.wav")
    library.scan()
    same = [t for t in alice.get("/api/search", params={"q": "same"}).json()["items"] if t["title"] == "Same"]
    assert len(same) == 2 and len({t["id"] for t in same}) == 2


def test_removed_import_files_leave_the_library(alice, lib):
    f = library.IMPORT_DIR / "Gone Artist - Gone Song.wav"
    write_wav(f)
    library.scan()
    assert "Gone Song" in titles(alice)
    f.unlink()
    library.scan()
    assert "Gone Song" not in titles(alice)


def test_import_signature_notices_new_files(lib):
    before = library.import_signature()
    time.sleep(0.01)
    write_wav(library.IMPORT_DIR / "A - B.wav")
    assert library.import_signature() != before
    assert library.import_signature()[0] == before[0] + 1


def test_plugin_folder_is_not_read_as_artist_or_album(alice, lib):
    import db
    with db.db() as c:
        c.execute("INSERT INTO plugins(id, name, url, token, version, description, enabled, added_at) VALUES('radio','R','','','1','d',1,0)")
    write_wav(lib / "radio" / "Solo - Flat Song.wav")
    write_wav(lib / "radio" / "Band" / "Album" / "Deep Song.wav")
    library.scan()
    rows = {t["title"]: t for t in alice.get("/api/search", params={"q": "song"}).json()["items"]}
    assert rows["Flat Song"]["artist"] == "Solo" and rows["Flat Song"]["album"] == ""  # not album "radio"
    assert rows["Deep Song"]["artist"] == "Band" and rows["Deep Song"]["album"] == "Album"


def test_import_root_follows_whether_the_music_folder_is_writable(lib, monkeypatch):
    assert library.import_root() == lib
    monkeypatch.setattr(library.os, "access", lambda p, mode: False)
    assert library.import_root() == library.IMPORT_DIR


def test_plugins_get_their_own_folder_in_the_import_root(lib):
    import pluginhost
    from pathlib import Path
    env = pluginhost._child_env("radio", Path("/usr/bin/python3"), "t")
    assert env["PLUGIN_IMPORT_DIR"] == str(lib / "radio") and (lib / "radio").is_dir()


def test_watcher_notices_files_in_a_plugin_folder_of_the_music_folder(lib):
    import db
    with db.db() as c:
        c.execute("INSERT INTO plugins(id, name, url, token, version, description, enabled, added_at) VALUES('radio','R','','','1','d',1,0)")
    before = library.import_signature()
    write_wav(lib / "radio" / "A - B.wav")
    assert library.import_signature()[0] == before[0] + 1
