import main
from conftest import PW, ids_by_title, make_client, vid


def track(n: int, **extra):
    t = {"id": vid(n), "title": f"Song {n}", "artist": "Artist A", "artist_id": "aaaaaaaaaaaaaaa",
         "album": "Album X", "album_id": "bbbbbbbbbbbbbbb", "duration": "3:30", "thumb": f"https://img.example/{n}.jpg"}
    t.update(extra)
    return t


# ---------- Login ----------

def test_login_sets_cookie_and_me_works(anon):
    main.db.create_user("Jane Test", PW)
    r = anon.post("/api/login", json={"name": "  Jane   Test ", "password": PW})
    assert r.status_code == 200
    assert r.json()["user"]["name"] == "Jane Test"
    assert anon.get("/api/me").json()["user"]["name"] == "Jane Test"


def test_login_same_name_same_user_case_insensitive():
    a = make_client("Casey")
    b = make_client("casey")
    assert a.get("/api/me").json()["user"]["id"] == b.get("/api/me").json()["user"]["id"]


def test_login_has_no_registration(anon):
    """Unknown names are not created, not even after setup."""
    main.db.create_user("Chef", PW)
    assert anon.post("/api/login", json={"name": "Fremder", "password": PW}).status_code == 401
    assert main.db.find_user("Fremder") is None


def test_logout_ends_session(anon):
    main.db.create_user("Dana", PW)
    anon.post("/api/login", json={"name": "Dana", "password": PW})
    anon.post("/api/logout")
    assert anon.get("/api/me").status_code == 401


def test_personal_endpoints_need_login(anon):
    for path in ["/api/me", "/api/playlists", "/api/home"]:
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/me/played", json=track(1)).status_code == 401


# ---------- Playlists ----------

def test_playlist_lifecycle(alice):
    pid = alice.post("/api/playlists", json={"name": "Meine Liste", "tracks": [track(1)]}).json()["id"]
    assert alice.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(1), track(2)]}).json() == {"added": 1}  # duplicate skipped
    p = alice.get(f"/api/playlists/{pid}").json()
    assert [t["id"] for t in p["tracks"]] == [vid(1), vid(2)]
    listing = alice.get("/api/playlists").json()["playlists"]
    assert listing[0]["count"] == 2 and listing[0]["thumb"].startswith("https://")
    assert alice.patch(f"/api/playlists/{pid}", json={"name": "Neu"}).status_code == 200
    assert alice.delete(f"/api/playlists/{pid}/tracks/{p['tracks'][0]['entry_id']}").status_code == 200
    assert len(alice.get(f"/api/playlists/{pid}").json()["tracks"]) == 1
    assert alice.delete(f"/api/playlists/{pid}").status_code == 200
    assert alice.get(f"/api/playlists/{pid}").status_code == 404


def test_playlists_are_private(alice, bob):
    pid = alice.post("/api/playlists", json={"name": "Geheim", "tracks": [track(1)]}).json()["id"]
    assert bob.get("/api/playlists").json()["playlists"] == []
    assert bob.get(f"/api/playlists/{pid}").status_code == 404
    assert bob.patch(f"/api/playlists/{pid}", json={"name": "x"}).status_code == 404
    assert bob.delete(f"/api/playlists/{pid}").status_code == 404
    assert bob.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(2)]}).status_code == 404
    assert len(alice.get(f"/api/playlists/{pid}").json()["tracks"]) == 1


def test_track_validation(alice):
    pid = alice.post("/api/playlists", json={"name": "V"}).json()["id"]
    assert alice.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(1, id="kurz")]}).status_code == 422
    alice.post(f"/api/playlists/{pid}/tracks", json={"tracks": [track(1, thumb="javascript:alert(1)")]})
    assert alice.get(f"/api/playlists/{pid}").json()["tracks"][0]["thumb"] == ""


# ---------- History and home page ----------

def _played(client, ids, title):
    t = next(x for x in client.get("/api/search", params={"q": title}).json()["items"])
    client.post("/api/me/played", json=t)
    return t


def test_home_without_history_shows_new_and_random(alice, lib):
    home = alice.get("/api/home").json()
    assert home["empty"] is False
    assert [s["title"] for s in home["shelves"]][:1] == ["Recently added"]


def test_home_empty_library_is_empty(alice):
    assert alice.get("/api/home").json()["empty"] is True


def test_home_personalised_after_listening(alice, lib):
    ids = ids_by_title(alice)
    for title in ("Alpha", "Alpha", "Beta"):
        _played(alice, ids, title)
    home = alice.get("/api/home").json()
    titles = [s["title"] for s in home["shelves"]]
    assert titles[0] == "Recently played" and "Your Mix" in titles
    recent = home["shelves"][0]["tracks"]
    assert [t["title"] for t in recent] == ["Beta", "Alpha"]  # unique, most recent first
    heard = {ids["Alpha"], ids["Beta"]}
    for shelf in home["shelves"][1:]:
        assert not heard & {t["id"] for t in shelf["tracks"]}
    assert home["artists"][0]["name"] == "Artist A"


def test_home_updates_after_new_play(alice, lib):
    ids = ids_by_title(alice)
    _played(alice, ids, "Alpha")
    first = alice.get("/api/home").json()
    assert alice.get("/api/home").json() == first  # from the cache
    _played(alice, ids, "Epsilon")
    assert alice.get("/api/home").json()["shelves"][0]["tracks"][0]["title"] == "Epsilon"


def test_home_drops_tracks_that_left_the_library(alice, lib):
    ids = ids_by_title(alice)
    _played(alice, ids, "Alpha")
    _played(alice, ids, "Epsilon")
    (lib / "b" / "z" / "01.wav").unlink()
    main.library.scan()
    recent = alice.get("/api/home?refresh=1").json()["shelves"][0]["tracks"]
    assert [t["title"] for t in recent] == ["Alpha"]


def test_home_is_per_user(alice, bob, lib):
    ids = ids_by_title(alice)
    _played(alice, ids, "Alpha")
    assert bob.get("/api/home").json()["shelves"][0]["title"] == "Recently added"


def test_invalid_ids_rejected(client):
    assert client.get("/api/stream/zu-kurz").status_code == 400
    assert client.get("/api/radio/zu-kurz").status_code == 400
    assert client.get("/api/artist/a b").status_code in (400, 404)


def test_home_shelf_order_and_covers_for_because_shelves(alice, lib):
    """Recently played, what is new, the mix, then the "because" shelves (each led by the cover of its song)."""
    ids = ids_by_title(alice)
    for title in ("Alpha", "Beta", "Gamma"):
        _played(alice, ids, title)
    shelves = alice.get("/api/home").json()["shelves"]
    rank = {"recent": 0, "new": 1, "mix": 2, "because": 3}
    kinds = [s["kind"] for s in shelves]
    assert kinds[0] == "recent" and kinds == sorted(kinds, key=rank.get)
    assert all("thumb" in s for s in shelves if s["kind"] == "because")
