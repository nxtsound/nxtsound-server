"""Widgets of the home page: the catalog, a user's own layout (order, add, remove, options), and the data each widget delivers."""
import homewidgets
import main
from conftest import make_client


def _play(c, title):
    t = next(x for x in c.get("/api/search", params={"q": title}).json()["items"] if x["title"] == title)
    assert c.post("/api/me/played", json=t).status_code == 200
    return t


def _home(c, **params):
    return c.get("/api/home", params={"refresh": 1, **params}).json()


def _types(home):
    return [w["type"] for w in home["widgets"]]


def _put(c, layout):
    return c.put("/api/home/layout", json={"layout": layout})


def _w(type_, **opts):
    return {"id": type_, "type": type_, "opts": opts}


def test_the_catalog_lists_every_widget_with_its_options(alice):
    catalog = alice.get("/api/home/widgets").json()["catalog"]
    by_type = {c["type"]: c for c in catalog}
    assert set(by_type) == set(homewidgets.specs()) and len(catalog) >= 16
    assert all(c["title"] and c["description"] and isinstance(c["options"], list) for c in catalog)
    assert by_type["top"]["multiple"] and not by_type["recent"]["multiple"]
    period = next(o for o in by_type["top"]["options"] if o["key"] == "period")
    assert period["type"] == "choice" and period["default"] == "month" and {c["value"] for c in period["choices"]} == {"week", "month", "year", "all"}


def test_without_a_layout_of_their_own_users_get_the_default(alice):
    state = alice.get("/api/home/layout").json()
    assert state["custom"] is False
    assert [e["type"] for e in state["layout"]] == ["recent", "new", "mix", "friends", "because", "artists"]


def test_a_saved_layout_decides_order_and_selection(alice, lib):
    for title in ("Alpha", "Beta", "Gamma"):
        _play(alice, title)
    assert _put(alice, [_w("artists"), _w("recent"), _w("likes")]).json()["custom"] is True
    home = _home(alice)
    assert _types(home) == ["artists", "recent"]  # likes: nothing liked yet, so nothing to show
    assert home["custom"] is True
    assert alice.get("/api/home/layout").json()["layout"][0]["type"] == "artists"


def test_reset_brings_the_default_back(alice, lib):
    _play(alice, "Alpha")
    _put(alice, [_w("artists")])
    assert alice.delete("/api/home/layout").json() == {"custom": False, "layout": homewidgets.clean_layout(homewidgets.DEFAULT_LAYOUT)}
    assert _types(_home(alice))[0] == "recent"


def test_layouts_belong_to_their_user(alice, bob, lib):
    _put(alice, [_w("discover")])
    assert bob.get("/api/home/layout").json()["custom"] is False
    assert _types(_home(bob)) == ["new"]  # no history yet; the random selection would only repeat the few new songs of this tiny library


def test_options_are_kept_in_range(alice):
    saved = _put(alice, [_w("new", count=999), _w("recent", pages="two"), _w("top", period="decade", count=1)]).json()["layout"]
    assert saved[0]["opts"] == {"count": 24} and saved[1]["opts"] == {"pages": 3} and saved[2]["opts"] == {"period": "month", "count": 6}


def test_an_option_changes_what_comes_back(alice, lib):
    for title in ("Alpha", "Beta", "Gamma", "Delta Single"):
        _play(alice, title)
    _put(alice, [_w("recent", pages=1)])
    assert len(_home(alice)["widgets"][0]["tracks"]) == 4
    _put(alice, [_w("new", count=6)])
    assert len(_home(alice)["widgets"][0]["tracks"]) <= 6


def test_a_bad_layout_is_refused(alice):
    assert _put(alice, [{"id": "x", "type": "nope", "opts": {}}]).status_code == 422
    assert _put(alice, [_w("recent"), {"id": "other", "type": "recent", "opts": {}}]).status_code == 422  # only once
    assert _put(alice, [_w("recent")] * 31).status_code == 422
    assert alice.put("/api/home/layout", json={"layout": "recent"}).status_code == 422
    assert alice.put("/api/home/layout", json={}).status_code == 422
    assert alice.get("/api/home/layout").json()["custom"] is False  # nothing was saved


def test_some_widgets_may_be_added_twice(alice):
    layout = _put(alice, [{"id": "a", "type": "top", "opts": {"period": "week"}}, {"id": "b", "type": "top", "opts": {"period": "year"}}]).json()["layout"]
    assert [e["id"] for e in layout] == ["a", "b"]
    again = _put(alice, [{"type": "top", "opts": {}}, {"type": "top", "opts": {}}]).json()["layout"]  # no IDs given: they are made up, and differ
    assert len({e["id"] for e in again}) == 2


def test_a_stored_layout_from_another_version_is_cleaned(alice):
    main.db.set_home_layout(main.db.find_user("Alice")["id"], '[{"id":"r","type":"recent","opts":{}},{"id":"z","type":"from-the-future","opts":{}}]')
    assert [e["type"] for e in alice.get("/api/home/layout").json()["layout"]] == ["recent"]
    main.db.set_home_layout(main.db.find_user("Alice")["id"], "not json")
    assert alice.get("/api/home/layout").json()["custom"] is False


def test_the_widgets_that_need_listening_wait_for_it(alice, lib):
    home = _home(alice)  # no history
    assert _types(home) == ["new"] and home["empty"] is False
    assert alice.delete("/api/home/layout").status_code == 200


def test_likes_playlists_top_and_recap_widgets(alice, lib):
    tracks = [_play(alice, t) for t in ("Alpha", "Beta", "Gamma", "Delta Single")]
    alice.post("/api/likes", json=tracks[0])
    pid = alice.post("/api/playlists", json={"name": "Road trip", "tracks": tracks[:2]}).json()["id"]
    _put(alice, [_w("likes"), _w("playlists"), _w("top", period="week"), _w("stats", period="week"), _w("discover")])
    by_type = {w["type"]: w for w in _home(alice)["widgets"]}
    assert by_type["likes"]["action"] == "shuffleLikes" and by_type["likes"]["count"] == 1
    assert [p["id"] for p in by_type["playlists"]["items"]] == [pid] and by_type["playlists"]["items"][0]["name"] == "Road trip"
    assert len(by_type["top"]["tracks"]) >= 3 and by_type["top"]["title"] == "Top songs this week"
    assert by_type["stats"]["plays"] == 4 and by_type["stats"]["top_artist"] == "Artist A" and by_type["stats"]["nav"] == "#/account/recap?p=week"
    assert by_type["discover"]["tracks"]


def test_tiles_come_from_the_automatic_lists(alice, lib):
    _play(alice, "Alpha")
    _put(alice, [_w("genres"), _w("decades"), _w("artistmix")])
    by_type = {w["type"]: w for w in _home(alice)["widgets"]}
    assert any(i["id"] == "genre:Rock" for i in by_type["genres"]["items"])
    assert any(i["id"] == "decade:2020" for i in by_type["decades"]["items"])


def test_a_year_ago_and_the_time_of_day(alice, lib):
    for title in ("Alpha", "Beta", "Gamma", "Delta Single", "Epsilon"):
        _play(alice, title)
    _put(alice, [_w("timeofday"), _w("onthisday")])
    home = _home(alice)
    assert _types(home) == ["timeofday"] and len(home["widgets"][0]["tracks"]) >= 4  # all plays are from now
    with main.db.db() as c:
        c.execute("UPDATE plays SET played_at = played_at - ?", (365 * 86400,))
    home = _home(alice)
    assert _types(home) == ["onthisday"] and home["widgets"][0]["title"] == "A year ago"  # now they are a year old


def test_the_time_of_day_follows_the_users_clock(alice, lib):
    for title in ("Alpha", "Beta", "Gamma", "Delta Single"):
        _play(alice, title)
    _put(alice, [_w("timeofday")])
    assert _home(alice)["widgets"][0]["title"].endswith("mix")
    with main.db.db() as c:
        c.execute("UPDATE plays SET played_at = played_at - 43200")  # twelve hours ago: another part of the day
    assert _home(alice)["widgets"] == []
    far = _home(alice, tz=720)  # the user's clock decides which plays belong to this time of day: with another clock they fit again
    assert far["widgets"] == [] or far["widgets"][0]["type"] == "timeofday"


def test_titles_are_in_the_users_language(alice, lib):
    _play(alice, "Alpha")
    alice.cookies.set("nx_lang", "de")
    assert next(w for w in _home(alice)["widgets"] if w["type"] == "new")["title"] == "Neu hinzugefügt"
    assert {c["type"]: c["title"] for c in alice.get("/api/home/widgets").json()["catalog"]}["recent"] == "Zuletzt gehört"


def test_friends_widget_only_with_community(alice, bob, lib):
    _play(alice, "Alpha")
    _put(alice, [_w("friends")])
    assert _home(alice)["widgets"] == []  # community is off
    alice.put("/api/admin/community", json={"enabled": True})
    w = _home(alice)["widgets"][0]
    assert w["type"] == "friends" and w["items"] == [] and w["following"] == 0


def test_the_home_calls_need_a_login(anon):
    assert anon.get("/api/home").status_code == 401
    assert anon.get("/api/home/layout").status_code == 401
    assert anon.get("/api/home/widgets").status_code == 401
    assert anon.put("/api/home/layout", json={"layout": []}).status_code in (401, 403)
