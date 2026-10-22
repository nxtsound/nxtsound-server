"""Translations: every translatable string has a German entry, placeholders match, language resolution works."""
import json
import re
from pathlib import Path

import i18n
import i18n_keys
from conftest import make_client

CATALOG = json.loads((Path(__file__).resolve().parent.parent / "static" / "i18n" / "de.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_source_string_has_a_german_translation():
    missing = sorted(i18n_keys.all_keys() - set(CATALOG))
    assert missing == [], f"add these to static/i18n/de.json: {missing}"


def test_catalog_has_no_unused_entries():
    unused = sorted(set(CATALOG) - i18n_keys.all_keys())
    assert unused == [], f"remove these from static/i18n/de.json: {unused}"


def test_placeholders_and_markup_match_between_languages():
    for en, de in CATALOG.items():
        assert set(PLACEHOLDER.findall(en)) == set(PLACEHOLDER.findall(de)), (en, de)
        assert re.findall(r"</?b>", en) == re.findall(r"</?b>", de), (en, de)
        assert en.count("\n") == de.count("\n"), (en, de)


def test_accept_language_parsing():
    assert i18n.parse_accept_language("de-CH,de;q=0.9,en;q=0.8") == "de"
    assert i18n.parse_accept_language("fr-FR,fr;q=0.9,en;q=0.5") == "en"
    assert i18n.parse_accept_language("en;q=0.4, de;q=0.9") == "de"
    assert i18n.parse_accept_language("fr, it") == ""
    assert i18n.parse_accept_language("") == ""
    assert i18n.resolve("de", "en") == "de" and i18n.resolve("", "de-DE") == "de" and i18n.resolve("xx", "") == i18n.DEFAULT_LANG


def test_tr_translates_and_formats():
    i18n.set_lang("de")
    try:
        assert i18n.tr("Track not found") == "Titel nicht gefunden"
        assert i18n.tr("Password: at least {n} characters", n=8) == "Passwort: mindestens 8 Zeichen"
        assert i18n.trn(1, "{n} track", "{n} tracks") == "1 Titel"
        assert i18n.tr("Something not in the catalog") == "Something not in the catalog"
    finally:
        i18n.set_lang("en")
    assert i18n.tr("Track not found") == "Track not found" and i18n.trn(2, "{n} track", "{n} tracks") == "2 tracks"


def test_api_errors_follow_the_request_language(anon):
    de = anon.post("/api/login", json={"name": "x", "password": "y"}, headers={"Accept-Language": "de-DE,de;q=0.9"})
    en = anon.post("/api/login", json={"name": "x", "password": "y"}, headers={"Accept-Language": "en-US"})
    assert de.json()["detail"] == "Name oder Passwort falsch" and en.json()["detail"] == "Wrong name or password"
    anon.cookies.set("nx_lang", "de")
    cookie = anon.post("/api/login", json={"name": "x", "password": "y"}, headers={"Accept-Language": "en"})
    assert cookie.json()["detail"] == "Name oder Passwort falsch"  # explicit cookie wins over the browser language


def test_user_language_setting_is_saved_and_validated(alice):
    assert alice.get("/api/me").json()["user"]["lang"] == ""
    assert alice.put("/api/me/lang", json={"lang": "de"}).json() == {"lang": "de"}
    assert alice.get("/api/me").json()["user"]["lang"] == "de"
    assert alice.put("/api/me/lang", json={"lang": "fr"}).status_code == 422
    alice.put("/api/me/lang", json={"lang": ""})
    assert alice.get("/api/me").json()["user"]["lang"] == ""


def _lang(client, code):
    """Pretend the browser has chosen a language (the web UI sets this cookie)."""
    if code:
        client.cookies.set("nx_lang", code)
    else:
        client.cookies.delete("nx_lang")


def test_server_generated_texts_are_translated(alice, lib):
    en = alice.get("/api/home?refresh=1").json()
    assert [w["title"] for w in en["widgets"]] == ["Recently added"] and all(w["type"] and w["view"] for w in en["widgets"])
    assert {x["id"]: x["name"] for x in alice.get("/api/auto").json()["lists"]}["decade:2020"] == "The 2020s"
    assert alice.get("/api/search", params={"q": "no tags"}).json()["items"][0]["artist"] == "Unknown artist"
    artist_id = alice.get("/api/search", params={"q": "Alpha"}).json()["items"][0]["artist_id"]
    assert alice.get(f"/api/artist/{artist_id}").json()["subscribers"] == "4 tracks"
    _lang(alice, "de")
    assert [w["title"] for w in alice.get("/api/home").json()["widgets"]] == ["Neu hinzugefügt"]  # not served from the English cache
    names = {x["id"]: x["name"] for x in alice.get("/api/auto").json()["lists"]}
    assert names["genre:Rock"] == "Rock-Mix" and names["decade:2020"] == "Die 2020er"
    assert alice.get("/api/search", params={"q": "no tags"}).json()["items"][0]["artist"] == "Unbekannter Künstler"
    assert alice.get(f"/api/artist/{artist_id}").json()["subscribers"] == "4 Titel"


def test_news_follows_language(alice, monkeypatch):
    import main
    monkeypatch.setattr(main, "_news_entries", lambda: [{"version": "0.1.0", "items": [{"title": "Hello", "text": "Text", "title_de": "Hallo", "text_de": "Text DE"}]}])
    monkeypatch.setattr(main, "VERSION_INFO", {"version": "0.1.0", "commit": "x"})
    main.db.set_news_seen(alice.get("/api/me").json()["user"]["id"], "0.0.1")
    assert alice.get("/api/news").json()["items"] == [{"title": "Hello", "text": "Text"}]
    _lang(alice, "de")
    assert alice.get("/api/news").json()["items"] == [{"title": "Hallo", "text": "Text DE"}]
