"""Plugins: management (admin), passing on search/radio/stream/pages, protocol check."""
import json

import httpx
import pytest

import fakeplugin
import main
import plugincheck
import plugins
from conftest import make_client

URL = "http://fake-plugin.test"


@pytest.fixture()
def fake(monkeypatch):
    """Plugs the stand-in in as the network transport."""
    state = {"transport": fakeplugin.make()}
    monkeypatch.setattr(plugins, "_transport_factory", lambda pid: state["transport"])
    return state


@pytest.fixture()
def installed(alice, fake):
    r = alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN})
    assert r.status_code == 200, r.text
    return alice


def test_protocol_checker_accepts_fake_and_flags_broken_plugins():
    assert plugincheck.check(URL, fakeplugin.TOKEN, transport=fakeplugin.make()) == []
    bad = plugincheck.check(URL, fakeplugin.TOKEN, transport=fakeplugin.make(broken="search"))
    assert any("id" in p for p in bad)
    noauth = plugincheck.check(URL, fakeplugin.TOKEN, transport=fakeplugin.make(token=""))  # the plugin does not check the token
    assert any("401 or 403" in p for p in noauth)
    wrong = plugincheck.check(URL, fakeplugin.TOKEN, transport=fakeplugin.make(manifest_overrides={"api_version": 9}))
    assert any("protocol version" in p for p in wrong)


def test_admin_adds_lists_toggles_and_removes_plugin(alice, fake):
    r = alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN})
    p = r.json()
    assert r.status_code == 200 and p["id"] == "fake" and p["name"] == "Fake Source" and p["enabled"] and "token" not in p
    listing = alice.get("/api/admin/plugins").json()
    assert [x["id"] for x in listing["plugins"]] == ["fake"] and listing["api_version"] == 1
    assert "secret" not in alice.get("/api/admin/plugins").text
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 400  # doppelt
    assert alice.patch("/api/admin/plugins/fake", json={"enabled": False}).json()["enabled"] is False
    assert alice.post("/api/admin/plugins/fake/refresh").json()["version"] == "1.2.3"
    assert alice.delete("/api/admin/plugins/fake").status_code == 200
    assert alice.delete("/api/admin/plugins/fake").status_code == 404


def test_add_rejects_bad_token_foreign_sites_and_wrong_protocol(alice, fake):
    r = alice.post("/api/admin/plugins", json={"url": URL, "token": "wrong"})
    assert r.status_code == 400 and "token" in r.json()["detail"]
    fake["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, text="<html>Irgendeine Webseite</html>"))
    r = alice.post("/api/admin/plugins", json={"url": URL, "token": ""})
    assert r.status_code == 400 and "not a plugin" in r.json()["detail"]
    fake["transport"] = fakeplugin.make({"api_version": 2})
    assert "protocol version" in alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).json()["detail"]
    fake["transport"] = fakeplugin.make({"id": "local"})
    assert "reserved" in alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).json()["detail"]
    assert alice.post("/api/admin/plugins", json={"url": "ftp://x.example/", "token": ""}).status_code == 400
    assert alice.get("/api/admin/plugins").json()["plugins"] == []


def test_only_admins_manage_plugins(alice, fake):
    bob = make_client("Bob")
    assert bob.get("/api/admin/plugins").status_code == 403
    assert bob.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 403
    assert bob.delete("/api/admin/plugins/fake").status_code == 403


def test_search_merges_library_and_plugin_with_prefixed_ids(installed, lib):
    items = installed.get("/api/search", params={"q": "song"}).json()["items"]
    assert [t["title"] for t in items] == ["First Song", "Second Song"]
    t = items[0]
    assert t["id"] == "fake:t1" and t["artist_id"] == "fake:ar1" and t["album_id"] == "fake:al1" and t["source"] == "fake" and t["type"] == "track"
    both = installed.get("/api/search", params={"q": "a"}).json()["items"]
    assert any(x["id"].startswith("local:") for x in both) and any(x["id"] == "fake:t1" for x in both)
    assert [x["id"] for x in installed.get("/api/search", params={"q": "a", "source": "local"}).json()["items"] if x["id"].startswith("fake:")] == []
    only = installed.get("/api/search", params={"q": "a", "source": "fake"}).json()["items"]
    assert only and all(x["id"].startswith("fake:") for x in only)
    artists = installed.get("/api/search", params={"q": "fake", "kind": "artists"}).json()["items"]
    assert {"type": "artist", "id": "fake:ar1", "name": "Fake Band", "thumb": "", "source": "fake", "source_name": "Fake Source"} in artists
    albums = installed.get("/api/search", params={"q": "fake", "kind": "albums"}).json()["items"]
    assert any(a["id"] == "fake:al1" and a["type"] == "album" for a in albums)


def test_search_sources_can_be_fetched_separately(installed, lib):
    local = installed.get("/api/search", params={"q": "a", "source": "local"}).json()["items"]
    plug = installed.get("/api/search", params={"q": "a", "source": "plugins"}).json()["items"]
    assert local and all(x["id"].startswith("local:") for x in local)
    assert plug and all(x["id"].startswith("fake:") and x["source_name"] == "Fake Source" for x in plug)
    assert [x["id"] for x in installed.get("/api/search", params={"q": "a"}).json()["items"]] == [x["id"] for x in local + plug]


def test_disabled_or_broken_plugin_does_not_break_search(installed, lib, fake):
    installed.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert all(not x["id"].startswith("fake:") for x in installed.get("/api/search", params={"q": "a"}).json()["items"])
    installed.patch("/api/admin/plugins/fake", json={"enabled": True})
    fake["transport"] = httpx.MockTransport(lambda req: httpx.Response(500))
    r = installed.get("/api/search", params={"q": "alpha"})
    assert r.status_code == 200 and [t["title"] for t in r.json()["items"]] == ["Alpha"]
    assert "HTTP 500" in installed.get("/api/admin/plugins").json()["plugins"][0]["last_error"]


def test_radio_artist_album_pages_via_plugin(installed):
    tracks = installed.get("/api/radio/fake:t1").json()["tracks"]
    assert [t["id"] for t in tracks] == ["fake:t2"]
    a = installed.get("/api/artist/fake:ar1").json()
    assert a["name"] == "Fake Band" and a["description"] == "A test band" and [s["id"] for s in a["songs"]] == ["fake:t1", "fake:t2"]
    assert a["albums"][0]["id"] == "fake:al1"
    al = installed.get("/api/album/fake:al1").json()
    assert al["title"] == "Fake Album" and al["artist_id"] == "fake:ar1" and len(al["tracks"]) == 2
    assert installed.get("/api/artist/fake:gibtsnicht").status_code == 404
    assert installed.get("/api/radio/fake:gibtsnicht").json() == {"tracks": []}


def test_stream_is_proxied_with_range(installed):
    full = installed.get("/api/stream/fake:t1")
    assert full.status_code == 200 and full.headers["content-type"] == "audio/mpeg" and full.content == fakeplugin.AUDIO
    part = installed.get("/api/stream/fake:t1", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == fakeplugin.AUDIO[10:20] and part.headers["content-range"] == "bytes 10-19/2048"
    assert installed.get("/api/stream/fake:nope").status_code == 404
    assert installed.get("/api/stream/fake:t1", headers={"Range": "bytes=abc"}).status_code == 200  # an invalid Range is not forwarded


def test_plugin_tracks_work_in_playlists_likes_and_history(installed):
    t = installed.get("/api/search", params={"q": "first"}).json()["items"][0]
    pid = installed.post("/api/playlists", json={"name": "Mix", "tracks": [t]}).json()["id"]
    assert installed.get(f"/api/playlists/{pid}").json()["tracks"][0]["id"] == "fake:t1"
    assert installed.post("/api/likes", json=t).status_code == 200
    assert installed.post("/api/me/played", json=t).status_code == 200
    home = installed.get("/api/home").json()
    assert any(tr["id"] == "fake:t1" for s in home["shelves"] for tr in s["tracks"])  # "Recently played" also shows plugin tracks
    assert installed.get("/api/bpm/fake:t1").json() == {"ready": True, "usable": False}


def test_server_info_lists_plugins_function(anon):
    assert "plugins" in anon.get("/api/server-info").json()["functions"]


def _play(c, track, n=1):
    for _ in range(n):
        assert c.post("/api/me/played", json=track).status_code == 200


def test_home_recommends_from_plugin_radio_for_plugin_plays(installed):
    t1 = installed.get("/api/search", params={"q": "first", "source": "plugins"}).json()["items"][0]
    _play(installed, t1, 3)
    home = installed.get("/api/home?refresh=1").json()
    titles = {s["title"]: s for s in home["shelves"]}
    assert "Your Mix" in titles and [t["id"] for t in titles["Your Mix"]["tracks"]] == ["fake:t2"]  # the source's radio, not the library's
    assert any(s["title"].startswith("Because you listened to “First Song”") for s in home["shelves"])
    assert home["artists"][0]["id"] == "fake:ar1"


def test_auto_lists_include_plugin_tracks_and_artist_mix(installed):
    t1 = installed.get("/api/search", params={"q": "first", "source": "plugins"}).json()["items"][0]
    t2 = installed.get("/api/search", params={"q": "second", "source": "plugins"}).json()["items"][0]
    _play(installed, t1, 3)
    _play(installed, t2, 1)
    lists = {x["id"]: x for x in installed.get("/api/auto").json()["lists"]}
    assert "artist:fake:ar1" in lists and lists["artist:fake:ar1"]["name"] == "Mix: Fake Band"
    mix = installed.get("/api/auto/artist:fake:ar1").json()
    assert {t["id"] for t in mix["tracks"]} == {"fake:t1", "fake:t2"}
    _play(installed, t1, 1)
    _play(installed, t2, 1)
    top = installed.get("/api/auto/top").json()["tracks"]
    assert [t["id"] for t in top] == ["fake:t1", "fake:t2"]  # by frequency, even without a library entry


def test_suggestions_come_from_library_and_plugins(installed, lib):
    local = installed.get("/api/suggest", params={"q": "alp", "source": "local"}).json()["suggestions"]
    plug = installed.get("/api/suggest", params={"q": "alp", "source": "plugins"}).json()["suggestions"]
    assert local == ["Alpha"] and plug == ["alp live", "alp remix"]
    assert installed.get("/api/suggest", params={"q": "alp"}).json()["suggestions"] == ["Alpha", "alp live", "alp remix"]
    installed.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert installed.get("/api/suggest", params={"q": "alp"}).json()["suggestions"] == ["Alpha"]


def test_sources_lists_library_and_active_plugins(installed):
    assert installed.get("/api/sources").json()["sources"] == [{"id": "local", "name": "Library"}, {"id": "fake", "name": "Fake Source"}]
    installed.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert installed.get("/api/sources").json()["sources"] == [{"id": "local", "name": "Library"}]


def test_slow_suggestions_are_not_a_plugin_error_but_real_timeouts_are(installed, fake):
    def slow(req):
        raise httpx.ReadTimeout("timed out")
    fake["transport"] = httpx.MockTransport(slow)
    assert installed.get("/api/suggest", params={"q": "al"}).status_code == 200  # slow plugin: no suggestions, no error
    assert installed.get("/api/admin/plugins").json()["plugins"][0]["last_error"] == ""
    installed.get("/api/search", params={"q": "alpha"})
    err = installed.get("/api/admin/plugins").json()["plugins"][0]["last_error"]
    assert "No answer within" in err and "timeout" in err


# ---------- Settings and prefetch ----------

SCHEMA = [
    {"key": "ahead", "type": "int", "label": {"en": "Songs ahead", "de": "Titel im Voraus"}, "unit": {"en": "songs", "de": "Titel"}, "min": 0, "max": 10, "default": 2},
    {"key": "keep", "type": "bool", "label": "Keep songs", "help": {"en": "Keep them", "de": "Behalten"}, "default": False},
    {"key": "mode", "type": "choice", "label": "Mode", "default": "a", "depends_on": "keep",
     "choices": [{"value": "a", "label": "Mode A"}, {"value": "b", "label": {"en": "Mode B", "de": "Modus B"}}]},
]


@pytest.fixture()
def configurable(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"settings": SCHEMA, "features": ["search", "stream", "prefetch"]})
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 200
    return alice, fake["transport"]


def test_name_description_and_version_are_required(alice, fake):
    for missing in ("name", "description", "version"):
        manifest = {"id": "fake", "name": "N", "version": "1", "description": "D", "api_version": 1, "features": ["search"]}
        del manifest[missing]
        fake["transport"] = fakeplugin.make(manifest_overrides={**manifest, missing: ""})
        r = alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN})
        assert r.status_code == 400 and f"'{missing}' is required" in r.json()["detail"], missing


def test_settings_schema_is_checked(alice, fake):
    import pluginproto
    for bad in ([{"key": "x", "type": "slider", "label": "X", "default": 1}],
                [{"key": "x", "type": "int", "label": "X", "default": 50, "min": 0, "max": 10}],
                [{"key": "x", "type": "bool", "label": "", "default": True}],
                [{"key": "x", "type": "choice", "label": "X", "default": "z", "choices": [{"value": "a", "label": "A"}]}],
                [{"key": "x", "type": "bool", "label": "X", "default": True}, {"key": "x", "type": "bool", "label": "X", "default": True}],
                [{"key": "y", "type": "bool", "label": "Y", "default": True, "depends_on": "nope"}]):
        with pytest.raises(pluginproto.PluginError):
            pluginproto.validate_settings_schema(bad)
    assert len(pluginproto.validate_settings_schema(SCHEMA)) == 3


def test_settings_are_shown_saved_validated_and_pushed(configurable):
    alice, transport = configurable
    plugin = alice.get("/api/admin/plugins").json()["plugins"][0]
    assert [s["key"] for s in plugin["settings"]] == ["ahead", "keep", "mode"]
    v = alice.get("/api/admin/plugins/fake/settings").json()
    assert v["values"] == {"ahead": 2, "keep": False, "mode": "a"}
    assert v["schema"][0]["label"] == "Songs ahead" and v["schema"][0]["unit"] == "songs" and v["schema"][2]["depends_on"] == "keep"
    r = alice.put("/api/admin/plugins/fake/settings", json={"values": {"ahead": 5, "keep": True}})
    assert r.status_code == 200 and r.json()["values"] == {"ahead": 5, "keep": True, "mode": "a"}
    assert transport.calls[-1] == ("PUT", "/settings", {"values": {"ahead": 5, "keep": True, "mode": "a"}})
    assert '"ahead": 5' in r.json()["info"][0]["value"]
    for bad in ({"ahead": 11}, {"ahead": "3"}, {"keep": "yes"}, {"mode": "c"}, {"nope": 1}):
        r = alice.put("/api/admin/plugins/fake/settings", json={"values": bad})
        assert r.status_code == 400, bad
    assert alice.get("/api/admin/plugins/fake/settings").json()["values"]["ahead"] == 5  # invalid input changed nothing
    assert alice.get("/api/admin/plugins/nope/settings").status_code == 404


def test_settings_follow_the_language_and_need_admin(configurable):
    alice, _ = configurable
    de = alice.get("/api/admin/plugins/fake/settings", headers={"Accept-Language": "de"}).json()
    assert de["schema"][0]["label"] == "Titel im Voraus" and de["schema"][0]["unit"] == "Titel" and de["schema"][2]["choices"][1]["label"] == "Modus B"
    bob = make_client("Bob")
    assert bob.get("/api/admin/plugins/fake/settings").status_code == 403
    assert bob.put("/api/admin/plugins/fake/settings", json={"values": {}}).status_code == 403


def test_plugin_without_settings_has_none_to_show(installed):
    assert installed.get("/api/admin/plugins/fake/settings").status_code == 400
    assert installed.put("/api/admin/plugins/fake/settings", json={"values": {}}).status_code == 400


def test_removing_a_plugin_forgets_its_settings(configurable):
    alice, _ = configurable
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"ahead": 7}})
    alice.delete("/api/admin/plugins/fake")
    import db
    assert db.get_setting("plugin_settings:fake", "") == ""


def test_prefetch_hint_reaches_only_plugins_that_can_use_it(configurable, lib):
    alice, transport = configurable
    r = alice.post("/api/prefetch", json={"ids": ["fake:t1", "fake:t2", "local:xyz", "other:zzz", "bad"]})
    assert r.status_code == 200 and r.json() == {"sent": 2}
    assert transport.calls[-1] == ("POST", "/prefetch", {"ids": ["t1", "t2"]})
    assert alice.post("/api/prefetch", json={"ids": ["x:" + str(i) for i in range(21)]}).status_code == 422  # at most 20
    assert alice.post("/api/prefetch", json={"ids": []}).json() == {"sent": 0}
    assert make_client("Bob").post("/api/prefetch", json={"ids": ["fake:t1"]}).status_code == 200


def test_prefetch_needs_login_and_is_not_sent_to_plugins_without_the_feature(installed, anon):
    assert anon.post("/api/prefetch", json={"ids": ["fake:t1"]}).status_code == 401
    assert installed.post("/api/prefetch", json={"ids": ["fake:t1"]}).json() == {"sent": 0}  # the plain stand-in has no 'prefetch'


def test_protocol_check_covers_settings_and_prefetch():
    ok = fakeplugin.make(manifest_overrides={"settings": SCHEMA, "features": ["search", "stream", "prefetch"]})
    assert plugincheck.check(URL, fakeplugin.TOKEN, transport=ok) == []


# ---------- Search categories ----------

CATEGORIES = [{"id": "radio", "label": {"en": "Live radio", "de": "Live-Radio"}}]


@pytest.fixture()
def with_category(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"categories": CATEGORIES})
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 200
    return alice


def test_category_labels_come_in_the_users_language(with_category):
    assert with_category.get("/api/search-categories").json() == {"categories": [{"id": "radio", "label": "Live radio"}]}
    de = with_category.get("/api/search-categories", headers={"Accept-Language": "de"}).json()
    assert de == {"categories": [{"id": "radio", "label": "Live-Radio"}]}
    with_category.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert with_category.get("/api/search-categories").json() == {"categories": []}  # a turned-off plugin has no tabs


def test_category_results_appear_only_in_their_tab(with_category, lib):
    radio = with_category.get("/api/search", params={"q": "radio", "kind": "radio"}).json()
    assert radio["kind"] == "radio" and [(t["id"], t["title"], t["type"], t["source_name"]) for t in radio["items"][:1]] == [("fake:st1", "Radio One", "track", "Fake Source")]
    assert radio["items"][0]["live"] is True
    songs = with_category.get("/api/search", params={"q": "radio", "kind": "songs"}).json()["items"]
    assert all(not t["id"].endswith(":st1") for t in songs)  # not mixed into the songs tab
    assert with_category.get("/api/search", params={"q": "radio", "kind": "radio", "source": "local"}).json()["items"] == []
    assert with_category.get("/api/search", params={"q": "x", "kind": "podcasts"}).status_code == 400
    assert with_category.get("/api/stream/fake:st1").status_code == 200


def test_categories_are_validated():
    import pluginproto
    ok = {"id": "x1", "name": "N", "version": "1", "description": "D", "api_version": 1, "features": ["search"]}
    assert pluginproto.validate_manifest({**ok, "categories": CATEGORIES})["categories"] == CATEGORIES
    for bad in ([{"id": "songs", "label": "S"}], [{"id": "Radio", "label": "R"}], [{"id": "radio"}], [{"id": "radio", "label": ""}],
                [{"id": "radio", "label": "R"}, {"id": "radio", "label": "R"}], "radio", [{"id": f"c{i}x", "label": "L"} for i in range(5)]):
        with pytest.raises(pluginproto.PluginError):
            pluginproto.validate_manifest({**ok, "categories": bad})
    with pytest.raises(pluginproto.PluginError):
        pluginproto.validate_manifest({**ok, "features": ["stream"], "categories": CATEGORIES})  # a category needs search


def test_protocol_check_covers_categories():
    transport = fakeplugin.make(manifest_overrides={"categories": CATEGORIES, "search_kinds": [], "features": ["search", "stream"]})
    assert plugincheck.check(URL, fakeplugin.TOKEN, "radio", transport=transport) == []


def test_protocol_check_flags_a_live_flag_that_is_not_a_boolean():
    bad = plugincheck.check(URL, fakeplugin.TOKEN, "radio", transport=fakeplugin.make(manifest_overrides={"categories": CATEGORIES, "search_kinds": [], "features": ["search", "stream"]}, broken="live"))
    assert any("'live' must be true or false" in x for x in bad)


def test_a_flag_that_is_not_a_boolean_is_dropped(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"categories": CATEGORIES}, broken="live")
    alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN})
    items = alice.get("/api/search", params={"q": "x", "kind": "radio"}).json()["items"]
    assert [(i["id"], i.get("live")) for i in items] == [("fake:st1", True), ("fake:st2", None)]  # only a real boolean true counts


# ---------- Text fields and actions ----------

ACTIONS = [{"id": "add_thing", "label": {"en": "Add a thing", "de": "Ding hinzufügen"}, "help": {"en": "Adds it", "de": "Fügt es hinzu"},
            "fields": [{"key": "name", "type": "text", "label": {"en": "Name", "de": "Name"}, "required": True, "max_length": 20, "placeholder": "e.g. Radio One"},
                       {"key": "tags", "type": "text", "label": "Tags", "max_length": 40},
                       {"key": "loud", "type": "bool", "label": "Loud", "default": False}]}]
TEXT_SETTINGS = [{"key": "mirror", "type": "text", "label": "Mirror", "default": "a.example", "max_length": 50}]


@pytest.fixture()
def with_actions(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"actions": ACTIONS, "settings": TEXT_SETTINGS})
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 200
    return alice, fake["transport"]


def test_text_fields_and_actions_are_validated():
    import pluginproto
    s = pluginproto.validate_settings_schema(TEXT_SETTINGS + [{"key": "u", "type": "text", "label": "U"}])
    assert s[0]["default"] == "a.example" and s[0]["max_length"] == 50 and s[1]["default"] == "" and s[1]["max_length"] == 200 and s[1]["required"] is False
    for bad in ([{"key": "t", "type": "text", "label": "T", "max_length": 0}], [{"key": "t", "type": "text", "label": "T", "max_length": 401}],
                [{"key": "t", "type": "text", "label": "T", "default": 5}], [{"key": "t", "type": "text", "label": "T", "max_length": 3, "default": "toolong"}]):
        with pytest.raises(pluginproto.PluginError):
            pluginproto.validate_settings_schema(bad)
    ok = {"id": "x1", "name": "N", "version": "1", "description": "D", "api_version": 1, "features": ["search"]}
    assert pluginproto.validate_manifest({**ok, "actions": ACTIONS})["actions"][0]["fields"][0]["required"] is True
    for bad in ([{"id": "A b", "label": "L"}], [{"id": "go", "label": ""}], [{"id": "go", "label": "L"}, {"id": "go", "label": "L"}],
                [{"id": "go", "label": "L", "fields": "x"}], [{"id": f"act{i}", "label": "L"} for i in range(5)], "go"):
        with pytest.raises(pluginproto.PluginError):
            pluginproto.validate_manifest({**ok, "actions": bad})


def test_the_settings_sheet_gets_actions_with_texts_in_the_users_language(with_actions):
    alice, _ = with_actions
    de = alice.get("/api/admin/plugins/fake/settings", headers={"Accept-Language": "de"}).json()
    a = de["actions"][0]
    assert (a["id"], a["label"], a["help"]) == ("add_thing", "Ding hinzufügen", "Fügt es hinzu")
    assert [(f["key"], f["label"], f["type"], f.get("required"), f.get("max_length"), f.get("placeholder", "")) for f in a["fields"]][0] == ("name", "Name", "text", True, 20, "e.g. Radio One")
    assert de["schema"][0]["key"] == "mirror" and de["values"]["mirror"] == "a.example"


def test_an_action_runs_with_validated_values_and_reports_the_result(with_actions):
    alice, transport = with_actions
    r = alice.post("/api/admin/plugins/fake/actions/add_thing", json={"values": {"name": "  Radio X  ", "loud": True}})
    assert r.status_code == 200 and r.json() == {"ok": True, "message": "added Radio X"}
    assert transport.calls[-1] == ("POST", "/actions/add_thing", {"values": {"name": "Radio X", "tags": "", "loud": True}})  # trimmed, missing fields filled in
    assert alice.post("/api/admin/plugins/fake/actions/add_thing", json={"values": {"name": "refuse"}}).json() == {"ok": False, "message": "the source said no"}


def test_action_input_is_checked_before_the_plugin_is_asked(with_actions):
    alice, transport = with_actions
    before = len(transport.calls)
    for bad in ({}, {"name": "   "}, {"name": "x" * 21}, {"name": "ok", "loud": "yes"}, {"name": "ok", "nope": 1}):
        r = alice.post("/api/admin/plugins/fake/actions/add_thing", json={"values": bad})
        assert r.status_code == 400, bad
    assert alice.post("/api/admin/plugins/fake/actions/nope", json={"values": {}}).status_code == 400
    assert alice.post("/api/admin/plugins/nope/actions/add_thing", json={"values": {}}).status_code == 404
    assert len(transport.calls) == before  # nothing reached the plugin


def test_only_admins_run_actions(with_actions):
    assert make_client("Bob").post("/api/admin/plugins/fake/actions/add_thing", json={"values": {"name": "x"}}).status_code == 403


def test_text_settings_can_be_saved(with_actions):
    alice, transport = with_actions
    assert alice.put("/api/admin/plugins/fake/settings", json={"values": {"mirror": " b.example "}}).json()["values"] == {"mirror": "b.example"}
    assert transport.calls[-1] == ("PUT", "/settings", {"values": {"mirror": "b.example"}})
    assert alice.put("/api/admin/plugins/fake/settings", json={"values": {"mirror": "x" * 51}}).status_code == 400


def test_a_plugin_with_only_actions_still_has_a_sheet(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"actions": ACTIONS})
    alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN})
    v = alice.get("/api/admin/plugins/fake/settings").json()
    assert v["schema"] == [] and v["info"] == [] and len(v["actions"]) == 1


def test_an_action_message_may_come_in_two_languages(with_actions):
    alice, _ = with_actions
    assert alice.post("/api/admin/plugins/fake/actions/add_thing", json={"values": {"name": "german"}}).json()["message"] == "added"
    r = alice.post("/api/admin/plugins/fake/actions/add_thing", json={"values": {"name": "german"}}, headers={"Accept-Language": "de"})
    assert r.json()["message"] == "hinzugefügt"


# ---------- Now playing ----------

@pytest.fixture()
def live_plugin(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"categories": CATEGORIES, "search_kinds": [], "features": ["search", "stream", "nowplaying"]})
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 200
    plugins._now_cache.clear()
    return alice, fake["transport"]


def test_now_playing_comes_from_the_plugin_and_is_reused_for_a_few_seconds(live_plugin):
    alice, transport = live_plugin
    assert alice.get("/api/nowplaying/fake:st1").json() == {"title": "Fake Song", "artist": "Fake Artist"}
    assert alice.get("/api/nowplaying/fake:st1").json() == {"title": "Fake Song", "artist": "Fake Artist"}
    assert [c for c in transport.calls if c[1].startswith("/nowplaying/")] == [("GET", "/nowplaying/st1", None)]  # the second answer came from the cache
    assert make_client("Bob").get("/api/nowplaying/fake:st1").json()["title"] == "Fake Song"  # any logged-in user may ask


def test_now_playing_is_empty_when_unknown(live_plugin):
    alice, _ = live_plugin
    assert alice.get("/api/nowplaying/fake:st2").json() == {}  # the plugin does not know it
    assert alice.get("/api/nowplaying/local:abc").json() == {} and alice.get("/api/nowplaying/other:x").json() == {}


def test_now_playing_needs_login_and_a_plugin_that_offers_it(anon, installed):
    assert anon.get("/api/nowplaying/fake:st1").status_code == 401
    assert installed.get("/api/nowplaying/fake:t1").json() == {}  # the plain stand-in has no 'nowplaying'


def test_protocol_check_covers_now_playing():
    ok = fakeplugin.make(manifest_overrides={"categories": CATEGORIES, "search_kinds": [], "features": ["search", "stream", "nowplaying"]})
    assert plugincheck.check(URL, fakeplugin.TOKEN, "radio", transport=ok) == []


# ---------- Secret text settings ----------

SECRET_SETTINGS = [{"key": "user", "type": "text", "label": "User", "default": ""},
                   {"key": "password", "type": "text", "label": "Password", "secret": True, "default": ""}]


@pytest.fixture()
def with_secret(alice, fake):
    fake["transport"] = fakeplugin.make(manifest_overrides={"settings": SECRET_SETTINGS})
    assert alice.post("/api/admin/plugins", json={"url": URL, "token": fakeplugin.TOKEN}).status_code == 200
    return alice, fake["transport"]


def test_a_saved_secret_is_never_handed_out_again(with_secret):
    alice, transport = with_secret
    v = alice.get("/api/admin/plugins/fake/settings").json()
    assert [(f["key"], f.get("secret", False), f.get("has_value")) for f in v["schema"]] == [("user", False, None), ("password", True, False)]
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"user": "robin", "password": "s3cret!"}})
    assert transport.calls[-1] == ("PUT", "/settings", {"values": {"user": "robin", "password": "s3cret!"}})  # the plugin does get it
    v = alice.get("/api/admin/plugins/fake/settings").json()
    assert v["values"] == {"user": "robin", "password": ""} and v["schema"][1]["has_value"] is True
    view = alice.get("/api/admin/plugins/fake/settings").json()
    assert "s3cret!" not in json.dumps({k: view[k] for k in ("schema", "values", "actions")})  # (the stand-in's own info text echoes what it received; that is the plugin's business)
    assert "s3cret!" not in alice.get("/api/admin/plugins").text


def test_leaving_the_secret_empty_keeps_it_and_a_new_one_replaces_it(with_secret):
    alice, transport = with_secret
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"user": "a", "password": "first"}})
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"user": "b"}})  # the field is missing
    assert transport.calls[-1][2]["values"] == {"user": "b", "password": "first"}
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"user": "b", "password": ""}})  # or empty
    assert transport.calls[-1][2]["values"]["password"] == "first"
    alice.put("/api/admin/plugins/fake/settings", json={"values": {"user": "b", "password": "second"}})
    assert transport.calls[-1][2]["values"]["password"] == "second"


def test_secret_flag_is_validated_in_the_manifest():
    import pluginproto
    s = pluginproto.validate_settings_schema(SECRET_SETTINGS)
    assert s[0]["secret"] is False and s[1]["secret"] is True


def test_an_info_value_may_come_in_two_languages(configurable):
    alice, _ = configurable
    en = {i["label"]: i["value"] for i in alice.get("/api/admin/plugins/fake/settings").json()["info"]}
    de = {i["label"]: i["value"] for i in alice.get("/api/admin/plugins/fake/settings", headers={"Accept-Language": "de"}).json()["info"]}
    assert en["State"] == "fine" and de["Zustand"] == "gut"
