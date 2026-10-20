"""Library: scan, tags, covers, search, artist/album pages, radio, admin."""
import main
from conftest import PNG, ids_by_title, make_client, write_wav


def test_scan_reads_tags_and_derives_missing_ones(alice, lib):
    items = {t["title"]: t for t in alice.get("/api/search", params={"q": "a"}).json()["items"]}
    alpha = items["Alpha"]
    assert alpha["artist"] == "Artist A" and alpha["album"] == "Album X" and alpha["duration"] == "0:01"
    assert alpha["id"].startswith("local:") and alpha["artist_id"] and alpha["album_id"]
    # without tags: title from the file name, artist "Unknown"
    bare = alice.get("/api/search", params={"q": "no tags"}).json()["items"][0]
    assert bare["title"] == "no tags" and bare["artist"] == "Unknown artist"


def test_ids_are_stable_across_rescans(alice, lib):
    before = ids_by_title(alice)
    main.library.scan(full=True)
    assert ids_by_title(alice) == before


def test_rescan_picks_up_changes_additions_and_removals(alice, lib):
    write_wav(lib / "b" / "z" / "02.wav", title="Zeta", artist="Artist B", album="Album Z")
    (lib / "loose" / "no tags.wav").unlink()
    write_wav(lib / "a" / "x" / "02.wav", title="Beta (neu getaggt)", artist="Artist A", album="Album X", track="2/3")
    state = main.library.scan()
    assert state["added"] == 1 and state["removed"] == 1 and state["updated"] == 1
    titles = set(ids_by_title(alice))
    assert "Zeta" in titles and "no tags" not in titles and "Beta (neu getaggt)" in titles and "Beta" not in titles


def test_unchanged_files_are_not_reread(lib):
    state = main.library.scan()
    assert state["added"] == state["updated"] == state["removed"] == 0 and state["seen"] == 6


def test_hidden_and_non_audio_files_are_ignored(lib):
    (lib / ".versteckt.wav").write_bytes(b"x")
    (lib / "notiz.txt").write_text("hallo")
    (lib / ".cache").mkdir()
    write_wav(lib / ".cache" / "x.wav", title="Nein")
    assert main.library.scan()["seen"] == 6


def test_broken_audio_file_does_not_stop_the_scan(alice, lib):
    (lib / "broken.mp3").write_bytes(b"this is not an mp3")
    state = main.library.scan()
    assert state["errors"] == 0  # unreadable tags: title from the file name, the scan continues
    assert "broken" in ids_by_title(alice)


def test_search_kinds_and_terms(alice, lib):
    songs = alice.get("/api/search", params={"q": "artist a beta"}).json()["items"]  # terms may be spread across fields
    assert [t["title"] for t in songs] == ["Beta"] and songs[0]["type"] == "track"
    artists = alice.get("/api/search", params={"q": "artist", "kind": "artists"}).json()["items"]
    assert {a["name"] for a in artists} == {"Artist A", "Artist B"} and all(a["type"] == "artist" for a in artists)
    albums = alice.get("/api/search", params={"q": "album", "kind": "albums"}).json()["items"]
    assert {a["title"] for a in albums} == {"Album X", "Album Z"} and albums[0]["type"] == "album"
    assert alice.get("/api/search", params={"q": "gibtsnicht"}).json()["items"] == []


def test_search_escapes_wildcards(alice, lib):
    for q in ("%", "_", "a%b"):
        assert alice.get("/api/search", params={"q": q}).json()["items"] == []


def test_artist_page_splits_albums_and_singles(alice, lib):
    artist_id = alice.get("/api/search", params={"q": "Alpha"}).json()["items"][0]["artist_id"]
    a = alice.get(f"/api/artist/{artist_id}").json()
    assert a["name"] == "Artist A" and len(a["songs"]) == 4
    assert [x["title"] for x in a["albums"]] == ["Album X"] and [x["title"] for x in a["singles"]] == ["Single Y"]
    assert a["albums"][0]["thumb"].startswith("/api/cover/")
    assert alice.get("/api/artist/doesnotexistxx").status_code == 404


def test_album_page_orders_tracks_by_number(alice, lib):
    album_id = alice.get("/api/search", params={"q": "Alpha"}).json()["items"][0]["album_id"]
    al = alice.get(f"/api/album/{album_id}").json()
    assert al["title"] == "Album X" and al["artist"] == "Artist A" and al["year"] == "2020"
    assert [t["title"] for t in al["tracks"]] == ["Alpha", "Beta", "Gamma"]
    assert al["thumb"].startswith("/api/cover/") and al["duration"] == "1 minute"
    assert alice.get("/api/album/doesnotexistxx").status_code == 404


def test_cover_embedded_and_served(alice, lib):
    tid = ids_by_title(alice)["Alpha"]
    r = alice.get(f"/api/cover/{tid}")
    assert r.status_code == 200 and r.content == PNG and "max-age" in r.headers["cache-control"]
    assert alice.get(f"/api/cover/{ids_by_title(alice)['Beta']}").status_code == 404  # no cover


def test_folder_cover_is_used(alice, lib):
    (lib / "b" / "z" / "cover.jpg").write_bytes(PNG)
    main.library.scan(full=True)
    epsilon = alice.get("/api/search", params={"q": "Epsilon"}).json()["items"][0]
    assert epsilon["thumb"].startswith("/api/cover/") and alice.get(epsilon["thumb"]).status_code == 200


def test_radio_prefers_same_artist_and_never_contains_seed(alice, lib, monkeypatch):
    monkeypatch.setattr(main.library.random, "random", lambda: 0.0)  # without dice, otherwise Epsilon occasionally ends up before Delta
    ids = ids_by_title(alice)
    tracks = alice.get(f"/api/radio/{ids['Alpha']}", params={"limit": 3}).json()["tracks"]
    assert len(tracks) == 3 and ids["Alpha"] not in {t["id"] for t in tracks}
    assert {t["title"] for t in tracks} == {"Beta", "Gamma", "Delta Single"}  # all by the same artist first
    assert alice.get("/api/radio/local:00000000-0000-0000-0000-000000000000").json() == {"tracks": []}


def test_radio_fills_up_small_libraries_with_random_tracks(alice, lib):
    ids = ids_by_title(alice)
    tracks = alice.get(f"/api/radio/{ids['Epsilon']}", params={"limit": 50}).json()["tracks"]
    assert len(tracks) == 5 and ids["Epsilon"] not in {t["id"] for t in tracks}  # the library has 6 tracks


def test_admin_library_stats_and_rescan(alice, bob, lib):
    s = alice.get("/api/admin/library").json()
    assert s["tracks"] == 6 and s["artists"] == 3 and s["albums"] == 4 and s["scan"]["running"] is False
    write_wav(lib / "neu.wav", title="Neu")
    assert alice.post("/api/admin/library/scan").status_code == 200
    for _ in range(100):  # the scan runs in the background
        if not main.library.scan_state()["running"]:
            break
        __import__("time").sleep(0.05)
    assert alice.get("/api/admin/library").json()["tracks"] == 7
    assert bob.get("/api/admin/library").status_code == 403 and bob.post("/api/admin/library/scan").status_code == 403


def test_dashboard_reports_library(alice, lib):
    d = alice.get("/api/admin/dashboard").json()
    assert d["tracks"] == 6 and d["library"]["tracks"] == 6 and d["library_bytes"] > 0


def test_stream_never_leaves_the_music_folder(alice, lib, tmp_path):
    secret = tmp_path / "secret.wav"
    write_wav(secret, title="Geheim")
    with main.db.db() as c:
        c.execute("UPDATE lib_tracks SET path = ? WHERE title = 'Alpha'", ("../secret.wav",))
    tid = next(r for r in main.library.search_tracks("Alpha"))["id"]
    assert alice.get(f"/api/stream/{tid}").status_code == 404


def test_removed_file_returns_404_on_stream(alice, lib):
    tid = ids_by_title(alice)["Epsilon"]
    (lib / "b" / "z" / "01.wav").unlink()
    assert alice.get(f"/api/stream/{tid}").status_code == 404


def test_guess_from_folder_structure():
    g = main.library.guess_from_path("Queen/A Night at the Opera/03 - Love of My Life.mp3")
    assert (g["artist"], g["album"], g["title"], g["track_no"]) == ("Queen", "A Night at the Opera", "Love of My Life", 3)
    g = main.library.guess_from_path("Queen/1975 - A Night at the Opera/CD2/05 Bohemian Rhapsody.flac")
    assert (g["album"], g["year"], g["disc_no"], g["track_no"], g["title"]) == ("A Night at the Opera", "1975", 2, 5, "Bohemian Rhapsody")


def test_guess_from_filename_only():
    for rel in ("Daft Punk - Around the World.mp3", "Daft_Punk_-_Around_the_World.mp3",
                "Daft Punk-Around the World.mp3"):
        g = main.library.guess_from_path(rel)
        assert (g["artist"], g["title"]) == ("Daft Punk", "Around the World"), rel
    g = main.library.guess_from_path("Daft Punk - Discovery - 04 - Digital Love.mp3")
    assert (g["artist"], g["album"], g["track_no"], g["title"]) == ("Daft Punk", "Discovery", 4, "Digital Love")
    g = main.library.guess_from_path("99 Luftballons.mp3")
    assert g["title"] == "99 Luftballons" and g["track_no"] == 0
    g = main.library.guess_from_path("Spider-Man.mp3")
    assert g["title"] == "Spider" or g["artist"] == "Spider"  # deliberate: a single hyphen without spaces separates


def test_guess_filename_artist_with_album_folder():
    g = main.library.guess_from_path("Daft Punk/Daft Punk - Around the World.mp3")
    assert g["album"] == "" and g["artist"] == "Daft Punk"
    g = main.library.guess_from_path("Mixe/Daft Punk - Around the World.mp3")
    assert g["album"] == "Mixe" and g["artist"] == "Daft Punk"


def test_scan_uses_path_when_tags_missing(alice, lib):
    write_wav(lib / "Muse" / "Absolution" / "02 - Hysteria.wav")
    write_wav(lib / "Singles" / "Moby - Porcelain.wav")  # a folder that does not exist yet: names differing only in case are one folder on some file systems
    main.library.scan()
    t = {x["title"]: x for x in alice.get("/api/search", params={"q": "e"}).json()["items"] + alice.get("/api/search", params={"q": "o"}).json()["items"]}
    assert t["Hysteria"]["artist"] == "Muse" and t["Hysteria"]["album"] == "Absolution"
    assert t["Porcelain"]["artist"] == "Moby" and t["Porcelain"]["album"] == "Singles"


# ---------- Enrichment (MusicBrainz, without network) ----------

def test_enrich_fills_missing_year_cover_and_description_only(alice, lib, monkeypatch):
    import enrich
    write_wav(lib / "Muse" / "Absolution" / "02 - Hysteria.wav")
    main.library.scan()
    calls = []
    monkeypatch.setattr(enrich, "lookup_album", lambda artist, album: calls.append(("album", artist, album)) or
                        {"mbid": "m1", "year": "2003", "cover": PNG, "mime": "image/png"})
    monkeypatch.setattr(enrich, "lookup_artist", lambda name: calls.append(("artist", name)) or {"mbid": "m2", "facts": {"type": "Group", "area": "England", "begin": "1994", "end": "", "note": "", "genres": []}})
    enrich.set_enabled(True)
    res = enrich.run()
    assert res["albums_found"] == 3 and res["artists_found"] >= 1  # Muse, Album Z (no cover), Single Y
    # albums with year and cover from the tags are not looked up
    assert ("album", "Artist A", "Album X") not in calls and ("album", "Muse", "Absolution") in calls
    hy = next(t for t in alice.get("/api/search", params={"q": "hysteria"}).json()["items"])
    assert hy["thumb"].startswith("/api/cover/")
    al = alice.get(f"/api/album/{hy['album_id']}").json()
    assert al["year"] == "2003"
    ar = alice.get(f"/api/artist/{hy['artist_id']}").json()
    assert ar["description"] == "Band from England, since 1994."
    # second run: nothing left to do
    calls.clear()
    enrich.run()
    assert ("album", "Muse", "Absolution") not in calls
    # after a full re-scan the details found are kept
    main.library.scan(full=True)
    al = alice.get(f"/api/album/{hy['album_id']}").json()
    assert al["year"] == "2003" and al["tracks"][0]["thumb"]


def test_enrich_switch_and_network_error(alice, lib, monkeypatch):
    import enrich
    assert enrich.enabled() is False  # the tests run with ENRICH=0
    started = []
    monkeypatch.setattr(enrich, "maybe_run", lambda retry=False: started.append(retry))  # no background thread, no network
    assert alice.post("/api/admin/enrich", json={"enabled": True}).json()["enabled"] is True
    assert alice.get("/api/admin/enrich").json()["from_admin"] is True
    assert started == [False]
    alice.post("/api/admin/enrich", json={"enabled": False})
    monkeypatch.setattr(enrich, "lookup_artist", lambda name: (_ for _ in ()).throw(enrich.NetworkError("offline")))
    monkeypatch.setattr(enrich, "lookup_album", lambda a, b: None)
    enrich.set_enabled(True)
    res = enrich.run()
    assert res["error"] == "offline"
    assert enrich.status()["artists_missed"] == 0  # a network error is not a miss


def test_enrich_describe_artist():
    import enrich
    facts = enrich.artist_facts({"type": "Group", "area": {"name": "Germany"}, "life-span": {"begin": "1990"},
                                 "tags": [{"name": "rock", "count": 5}, {"name": "pop", "count": 9}]})
    assert enrich.describe(facts) == "Band from Germany, since 1990. Genres: pop, rock."
    from i18n import set_lang
    set_lang("de")
    try:
        assert enrich.describe(facts) == "Band aus Germany, seit 1990. Genres: pop, rock."
    finally:
        set_lang("en")


def test_enrich_admin_only(alice, lib):
    bob = make_client("Bob")
    assert bob.get("/api/admin/enrich").status_code == 403
