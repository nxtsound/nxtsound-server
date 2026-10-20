from conftest import ids_by_title, vid


def track(n, artist_id="aaaaaaaaaaaaaaa"):
    return {"id": vid(n), "title": f"Song {n}", "artist": "A", "artist_id": artist_id, "album": "", "album_id": "", "duration": "3:30", "thumb": ""}


# ---------- Remember position ----------

def test_position_only_for_long_tracks_and_resumes(alice):
    v = vid(40)
    assert alice.put("/api/me/position", json={"id": v, "pos": 120, "dur": 200}).json() == {"saved": False}  # short track
    assert alice.get(f"/api/me/position/{v}").json() == {"pos": 0, "dur": 0}
    assert alice.put("/api/me/position", json={"id": v, "pos": 5, "dur": 5400}).json() == {"saved": False}  # still at the start
    assert alice.put("/api/me/position", json={"id": v, "pos": 1500, "dur": 5400}).json() == {"saved": True}
    assert alice.get(f"/api/me/position/{v}").json() == {"pos": 1500, "dur": 5400}
    alice.put("/api/me/position", json={"id": v, "pos": 2000, "dur": 5400})
    assert alice.get(f"/api/me/position/{v}").json()["pos"] == 2000  # ueberschrieben
    assert alice.put("/api/me/position", json={"id": v, "pos": 5390, "dur": 5400}).json() == {"saved": False}  # finished
    assert alice.get(f"/api/me/position/{v}").json() == {"pos": 0, "dur": 0}


def test_position_is_per_user(alice, bob):
    v = vid(41)
    alice.put("/api/me/position", json={"id": v, "pos": 900, "dur": 4000})
    assert bob.get(f"/api/me/position/{v}").json() == {"pos": 0, "dur": 0}


def test_position_validates_input(alice):
    assert alice.put("/api/me/position", json={"id": "zu-kurz", "pos": 1, "dur": 2}).status_code == 422
    assert alice.put("/api/me/position", json={"id": vid(1), "pos": -1, "dur": 2}).status_code == 422


# ---------- Stop recommending ----------

def _radio(client, tid):
    return client.get(f"/api/radio/{tid}").json()["tracks"]


def test_dislike_track_and_artist_filter_radio(alice, lib):
    ids = ids_by_title(alice)
    tracks = _radio(alice, ids["Alpha"])
    assert {t["title"] for t in tracks} >= {"Beta", "Gamma"} and all(t["id"] != ids["Alpha"] for t in tracks)
    alice.post("/api/dislikes", json={"kind": "track", "id": ids["Beta"], "title": "Beta"})
    titles = {t["title"] for t in _radio(alice, ids["Alpha"])}
    assert "Beta" not in titles and "Gamma" in titles
    artist_id = next(t["artist_id"] for t in _radio(alice, ids["Alpha"]) if t["title"] == "Gamma")
    alice.post("/api/dislikes", json={"kind": "artist", "id": artist_id, "title": "Artist A"})
    assert all(t["artist_id"] != artist_id for t in _radio(alice, ids["Alpha"]))
    items = alice.get("/api/dislikes").json()["items"]
    assert {(i["kind"], i["id"]) for i in items} == {("track", ids["Beta"]), ("artist", artist_id)}


def test_dislike_is_per_user_and_removable(alice, bob, lib):
    ids = ids_by_title(alice)
    artist_id = next(t["artist_id"] for t in _radio(alice, ids["Alpha"]) if t["title"] == "Gamma")
    alice.post("/api/dislikes", json={"kind": "artist", "id": artist_id})
    assert any(t["artist_id"] == artist_id for t in _radio(bob, ids["Alpha"]))  # Bob sieht weiter alles
    assert all(t["artist_id"] != artist_id for t in _radio(alice, ids["Alpha"]))
    assert alice.delete(f"/api/dislikes/artist/{artist_id}").status_code == 200
    assert any(t["artist_id"] == artist_id for t in _radio(alice, ids["Alpha"]))
    assert alice.delete(f"/api/dislikes/artist/{artist_id}").status_code == 404


def test_dislike_removes_seed_from_home(alice, lib):
    ids = ids_by_title(alice)
    for title in ("Alpha", "Beta"):
        alice.post("/api/me/played", json=next(t for t in alice.get("/api/search", params={"q": title}).json()["items"]))
    before = alice.get("/api/home").json()
    assert any(sh["title"].startswith("Because you listened") for sh in before["shelves"])
    artist_id = next(t["artist_id"] for t in _radio(alice, ids["Alpha"]) if t["title"] == "Gamma")
    alice.post("/api/dislikes", json={"kind": "artist", "id": artist_id})  # both seeds are by this artist
    after = alice.get("/api/home").json()
    assert not any(sh["title"].startswith("Because you listened") for sh in after["shelves"])
    assert after["shelves"][0]["title"] == "Recently played"  # own history stays


def test_dislike_validates(alice):
    assert alice.post("/api/dislikes", json={"kind": "track", "id": "zu-kurz"}).status_code == 400
    assert alice.post("/api/dislikes", json={"kind": "foo", "id": "x"}).status_code == 422
