"""Playing at the same time: the limit per account counts players (tabs, apps), heartbeats, releasing, and what the admin sees."""
import main
from conftest import PW, Client


def _login(name="Alice"):
    c = Client(main.app)
    assert c.post("/api/login", json={"name": name, "password": PW}).status_code == 200
    return c


def _claim(c, player, what="claim"):
    return c.post(f"/api/player/{what}", json={"player": player})


def _limit(alice, n):
    r = alice.put("/api/admin/streams", json={"max_streams": n})
    assert r.status_code == 200
    return r.json()


def test_no_limit_by_default(alice):
    assert alice.get("/api/admin/streams").json()["max_streams"] == 0
    c = _login()
    assert all(_claim(c, f"player-{i:04d}").status_code == 200 for i in range(8))


def test_many_tabs_in_one_login_count_one_each(alice):
    """The point of a player ID: ten tabs in the same login are ten streams, not one."""
    _limit(alice, 3)
    c = _login()
    assert [_claim(c, f"tab-{i:06d}").status_code for i in range(3)] == [200, 200, 200]
    r = _claim(c, "tab-000099")
    assert r.status_code == 409 and "3 streams" in r.json()["detail"]
    assert _claim(c, "tab-000000", "beat").status_code == 200  # the ones that play are not disturbed


def test_another_login_is_told_no_too(alice):
    _limit(alice, 2)
    a, b, c = _login(), _login(), _login()
    assert _claim(a, "phone-0001").json() == {"ok": True, "active": 1, "limit": 2}
    assert _claim(b, "laptop-001").status_code == 200
    assert _claim(c, "tv-0000001").status_code == 409


def test_a_limit_of_one_has_its_own_wording(alice):
    _limit(alice, 1)
    a, b = _login(), _login()
    assert _claim(a, "player-aaaa").status_code == 200
    assert "one stream" in _claim(b, "player-bbbb").json()["detail"]


def test_claiming_twice_from_the_same_player_counts_once(alice):
    _limit(alice, 1)
    a = _login()
    assert _claim(a, "player-aaaa").status_code == 200
    assert _claim(a, "player-aaaa").status_code == 200
    assert _claim(a, "player-aaaa", "beat").json()["active"] == 1


def test_releasing_frees_the_place_and_only_that_one(alice):
    _limit(alice, 2)
    a = _login()
    _claim(a, "player-aaaa")
    _claim(a, "player-bbbb")
    assert _claim(a, "player-cccc").status_code == 409
    assert _claim(a, "player-aaaa", "release").status_code == 200
    assert _claim(a, "player-cccc").status_code == 200
    assert _claim(a, "player-dddd").status_code == 409  # b and c still hold theirs


def test_a_player_that_stops_beating_is_dropped(alice, monkeypatch):
    _limit(alice, 1)
    a, b = _login(), _login()
    _claim(a, "player-aaaa")
    monkeypatch.setattr(main.streamguard, "TTL", -1)  # as if the last heartbeat were long ago
    assert _claim(b, "player-bbbb").status_code == 200


def test_a_player_id_cannot_be_borrowed_across_logins(alice):
    """The same ID from two logins is two players (the place is per login and ID)."""
    _limit(alice, 2)
    a, b = _login(), _login()
    assert _claim(a, "same-id-001").status_code == 200
    assert _claim(b, "same-id-001").json()["active"] == 2


def test_the_limit_is_per_account(alice, bob):
    _limit(alice, 1)
    a, b = _login("Alice"), _login("Bob")
    assert _claim(a, "player-aaaa").status_code == 200
    assert _claim(b, "player-bbbb").status_code == 200  # someone else plays on their own


def test_the_admin_sees_who_plays_and_can_change_the_limit(alice):
    c = _login()
    _claim(c, "player-aaaa")
    _claim(c, "player-bbbb")
    state = alice.get("/api/admin/streams").json()
    assert state["playing"] == [{"user": "Alice", "streams": 2}] and state["max"] == 50
    assert _limit(alice, 3)["max_streams"] == 3


def test_lowering_the_limit_does_not_cut_off_a_playing_player(alice):
    a = _login()
    _claim(a, "player-aaaa"); _claim(a, "player-bbbb")
    _limit(alice, 1)
    assert _claim(a, "player-aaaa", "beat").status_code == 200  # keeps its place, nobody new gets one
    assert _claim(a, "player-cccc").status_code == 409


def test_pages_without_a_player_id_still_work(alice):
    """A page loaded before this feature sends no body: it counts as one player per login."""
    _limit(alice, 1)
    a, b = _login(), _login()
    assert a.post("/api/player/claim").status_code == 200
    assert a.post("/api/player/beat").status_code == 200
    assert b.post("/api/player/claim").status_code == 409
    assert a.post("/api/player/release").status_code == 200
    assert b.post("/api/player/claim").status_code == 200


def test_a_bad_player_id_is_refused(alice):
    a = _login()
    assert a.post("/api/player/claim", json={"player": "x"}).status_code == 422
    assert a.post("/api/player/claim", json={"player": "bad id with spaces!"}).status_code == 422
    assert a.post("/api/player/claim", json={"player": "a" * 65}).status_code == 422


def test_the_limit_is_validated_and_admin_only(alice, bob):
    assert alice.put("/api/admin/streams", json={"max_streams": -1}).status_code == 422
    assert alice.put("/api/admin/streams", json={"max_streams": 51}).status_code == 422
    assert bob.get("/api/admin/streams").status_code == 403
    assert bob.put("/api/admin/streams", json={"max_streams": 1}).status_code == 403


def test_a_broken_stored_limit_falls_back_to_the_default(alice):
    main.db.set_setting("max_streams", "lots")
    assert main.streamguard.limit() == main.streamguard.DEFAULT_LIMIT


def test_the_player_calls_need_a_login(anon):
    assert anon.post("/api/player/claim", json={"player": "player-aaaa"}).status_code in (401, 403)
    assert anon.post("/api/player/release", json={"player": "player-aaaa"}).status_code in (401, 403)
