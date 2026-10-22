from conftest import vid


def track(n: int, **extra):
    t = {"id": vid(n), "title": f"Song {n}", "artist": "Artist A", "artist_id": "aaaaaaaaaaaaaaa",
         "album": "Album X", "album_id": "bbbbbbbbbbbbbbb", "duration": "3:30", "thumb": f"https://img.example/{n}.jpg"}
    t.update(extra)
    return t


def queries(c):
    return [q["query"] for q in c.get("/api/searches").json()["queries"]]


# ---------- Suchverlauf ----------

def test_search_history_is_recorded_deduped_and_ordered(alice):
    for q in ["foo", "bar", "FOO"]:
        assert alice.get("/api/search", params={"q": q}).status_code == 200
    assert queries(alice) == ["FOO", "bar"]  # case-insensitively deduplicated, newest first


def test_search_history_is_private_and_deletable(alice, bob):
    alice.get("/api/search", params={"q": "secret"})
    assert queries(bob) == []
    sid = alice.get("/api/searches").json()["queries"][0]["id"]
    assert bob.delete(f"/api/searches/{sid}").status_code == 404
    assert alice.delete(f"/api/searches/{sid}").status_code == 200
    assert queries(alice) == []


def test_clear_history_removes_queries_and_picks(alice):
    alice.get("/api/search", params={"q": "x"})
    alice.post("/api/me/search-picks", json={"type": "artist", "id": "aaaaaaaaaaaaaaa", "title": "Artist A"})
    assert alice.delete("/api/searches").status_code == 200
    data = alice.get("/api/searches").json()
    assert data == {"queries": [], "picks": []}


def test_invalid_search_is_not_recorded(alice):
    assert alice.get("/api/search", params={"q": "broken", "kind": "playlists"}).status_code == 400
    assert queries(alice) == []


def test_picks_roundtrip_dedupe_and_validation(alice, bob):
    alice.post("/api/me/search-picks", json={"type": "track", "id": vid(1), "title": "Song 1", "subtitle": "Artist A",
                                              "thumb": "https://img.example/1.jpg", "track": track(1)})
    alice.post("/api/me/search-picks", json={"type": "album", "id": "bbbbbbbbbbbbbbb", "title": "Album X", "thumb": "javascript:x"})
    alice.post("/api/me/search-picks", json={"type": "track", "id": vid(1), "title": "Song 1 neu", "track": track(1)})  # Upsert
    picks = alice.get("/api/searches").json()["picks"]
    assert [p["id"] for p in picks] == [vid(1), "bbbbbbbbbbbbbbb"]  # most recently chosen first
    assert picks[0]["title"] == "Song 1 neu" and picks[0]["track"]["id"] == vid(1)
    assert picks[1]["thumb"] == "" and picks[1]["track"] is None
    assert alice.post("/api/me/search-picks", json={"type": "track", "id": vid(2), "title": "without track"}).status_code == 422
    assert alice.post("/api/me/search-picks", json={"type": "playlist", "id": "x"}).status_code == 422
    assert bob.get("/api/searches").json()["picks"] == []


def test_suggestions(alice, lib):
    r = alice.get("/api/suggest", params={"q": "alp"}).json()["suggestions"]
    assert r[0] == "Alpha"
    assert "Artist A" in alice.get("/api/suggest", params={"q": "artist"}).json()["suggestions"]
    assert alice.get("/api/suggest", params={"q": "gibtsnicht"}).json() == {"suggestions": []}


# ---------- Likes ----------

def test_like_roundtrip_is_idempotent_and_private(alice, bob):
    assert alice.post("/api/likes", json=track(1)).status_code == 200
    assert alice.post("/api/likes", json=track(1)).status_code == 200  # twice is fine
    alice.post("/api/likes", json=track(2))
    assert [t["id"] for t in alice.get("/api/likes").json()["tracks"]] == [vid(2), vid(1)]  # newest first
    assert bob.get("/api/likes").json()["tracks"] == []
    assert alice.delete(f"/api/likes/{vid(1)}").status_code == 200
    assert [t["id"] for t in alice.get("/api/likes").json()["tracks"]] == [vid(2)]
    assert alice.delete("/api/likes/zu-kurz").status_code == 400
    assert alice.post("/api/likes", json=track(3, id="kurz")).status_code == 422


def test_likes_boost_home_recommendations(alice, lib):
    alice.post("/api/me/played", json=next(t for t in alice.get("/api/search", params={"q": "Alpha"}).json()["items"]))
    epsilon = next(t for t in alice.get("/api/search", params={"q": "Epsilon"}).json()["items"])
    alice.post("/api/likes", json=epsilon)
    titles = [w["title"] for w in alice.get("/api/home").json()["widgets"]]
    assert any("Epsilon" in t for t in titles)  # the liked track serves as seed although it never played
