"""The same song in the library and from a plugin is listed once (and the library's copy is the one that plays)."""
import main
from conftest import ids_by_title, write_wav

PLUGIN = lambda i, title, artist, dur="0:01": {"id": f"yt:{i}", "title": title, "artist": artist, "duration": dur}  # noqa: E731


def test_a_plugin_song_with_the_same_title_and_artist_is_the_librarys(alice, lib):
    ids = ids_by_title(alice)
    got = main.library.twin_of(PLUGIN("a", "Alpha", "Artist A"))
    assert got == ids["Alpha"]
    assert main.library.twin_of(PLUGIN("b", "Alpha (feat. Somebody)", "Artist A, Somebody")) == ids["Alpha"]  # "feat." does not make another song
    assert main.library.twin_of(PLUGIN("c", "alpha", "ARTIST A")) == ids["Alpha"]  # case does not matter


def test_other_songs_are_not_twins(alice, lib):
    assert main.library.twin_of(PLUGIN("d", "Alpha", "Somebody Else")) is None  # same title, other artist
    assert main.library.twin_of(PLUGIN("e", "Alpha (Remix)", "Artist A")) is None  # a remix is a song of its own
    assert main.library.twin_of(PLUGIN("f", "Alpha", "Artist A", "3:30")) is None  # 3 minutes longer: another version
    assert main.library.twin_of({"id": "local:x", "title": "Alpha", "artist": "Artist A"}) is None  # a library song is not its own twin


def test_the_radio_list_has_the_librarys_song_instead_of_the_plugins_and_each_song_once(alice, lib):
    ids = ids_by_title(alice)
    tracks = [PLUGIN("a", "Alpha", "Artist A"), PLUGIN("n", "Not in the library", "Nobody"), PLUGIN("a2", "Alpha", "Artist A"), PLUGIN("n", "Not in the library", "Nobody")]
    out = main.library.prefer_library(tracks)
    assert [t["id"] for t in out] == [ids["Alpha"], "yt:n"]
    assert out[0]["artist"] == "Artist A" and out[0]["thumb"] is not None  # a complete library track
    assert [t["id"] for t in main.library.without_twins(tracks)] == ["yt:n", "yt:n"]


def test_a_new_library_song_is_found_without_a_restart(alice, lib):
    assert main.library.twin_of(PLUGIN("z", "Zeta", "Artist B")) is None
    write_wav(lib / "b" / "z" / "02.wav", title="Zeta", artist="Artist B", album="Album Z")
    main.library.scan()
    assert main.library.twin_of(PLUGIN("z", "Zeta", "Artist B")) == ids_by_title(alice)["Zeta"]
