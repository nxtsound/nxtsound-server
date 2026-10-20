"""DJ fit: tempo and key feed into radio/mixes, dj=1 orders into a smooth chain."""
import main
from conftest import ids_by_title

lib_mod = main.library


def _analysis(tid, bpm, key, conf=0.9):
    main.db.save_bpm(tid, {"bpm": bpm, "first_beat": 0.1, "conf": conf, "key": key, "key_conf": 0.8, "ver": 2})


def test_tempo_and_key_distance():
    assert lib_mod._tempo_diff(128, 128) == 0 and lib_mod._tempo_diff(64, 128) == 0  # halbes Tempo passt
    assert lib_mod._tempo_diff(120, 150) > 0.1
    assert lib_mod._key_dist("8A", "8A") == 0 and lib_mod._key_dist("8A", "9A") == 1 and lib_mod._key_dist("12A", "1A") == 1
    assert lib_mod._key_dist("8A", "8B") == 1 and lib_mod._key_dist("8A", "2A") == 6 and lib_mod._key_dist("", "8A") is None
    close = lib_mod.dj_affinity({"bpm": 124, "key": "8A"}, {"bpm": 126, "key": "9A"})
    far = lib_mod.dj_affinity({"bpm": 124, "key": "8A"}, {"bpm": 170, "key": "2B"})
    assert close > 1.8 and far == 0


def test_dj_order_chains_analysed_tracks_and_keeps_unanalysed_slots(alice, lib):
    ids = ids_by_title(alice)
    _analysis(ids["Alpha"], 120, "8A")
    _analysis(ids["Beta"], 170, "3B")      # weit weg
    _analysis(ids["Gamma"], 122, "9A")     # passt zu Alpha
    _analysis(ids["Delta Single"], 124, "9A")
    tracks = [lib_mod.get_track(ids[t]) for t in ("Beta", "Epsilon", "Delta Single", "Gamma")]  # Epsilon: not analyzed
    out = lib_mod.dj_order(tracks, ids["Alpha"])
    titles = [t["title"] for t in out]
    assert titles[1] == "Epsilon"                                   # Platz bleibt
    assert titles[0] in ("Gamma", "Delta Single") and titles[3] == "Beta"  # matching ones first, the outlier last
    assert sorted(titles) == sorted(["Beta", "Epsilon", "Delta Single", "Gamma"])


def test_dj_order_leaves_list_alone_without_data(alice, lib):
    ids = ids_by_title(alice)
    tracks = [lib_mod.get_track(ids[t]) for t in ("Beta", "Gamma", "Epsilon")]
    assert lib_mod.dj_order(tracks, ids["Alpha"]) == tracks


def test_similar_prefers_matching_tempo_and_key(alice, lib, monkeypatch):
    monkeypatch.setattr(lib_mod.random, "random", lambda: 0.0)
    ids = ids_by_title(alice)
    _analysis(ids["Alpha"], 120, "8A")
    _analysis(ids["Epsilon"], 121, "8A")   # different artist, but same genre + a perfect fit
    _analysis(ids["Delta Single"], 170, "2B")  # same artist, but a bad DJ fit
    ranked = [t["title"] for t in lib_mod.similar(ids["Alpha"], 5)]
    assert ranked.index("Epsilon") < ranked.index("Delta Single")


def test_radio_dj_flag(alice, lib):
    ids = ids_by_title(alice)
    for t, (bpm, key) in {"Alpha": (120, "8A"), "Beta": (170, "3B"), "Gamma": (122, "9A"), "Delta Single": (124, "9A"), "Epsilon": (121, "8A")}.items():
        _analysis(ids[t], bpm, key)
    plain = alice.get(f"/api/radio/{ids['Alpha']}", params={"limit": 4}).json()["tracks"]
    dj = alice.get(f"/api/radio/{ids['Alpha']}", params={"limit": 4, "dj": 1}).json()["tracks"]
    assert {t["id"] for t in plain} == {t["id"] for t in dj} and ids["Alpha"] not in {t["id"] for t in dj}
