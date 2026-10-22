"""Remote control: players publish what they play, other devices of the account list them and send commands that arrive through the event stream."""
import asyncio
import json

import main
import playerhub
from conftest import PW, Client

TRACK = {"id": "test:00000001", "title": "Song", "artist": "Band", "thumb": ""}


def _login(name="Alice", ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/18.0 Safari/605.1.15"):
    c = Client(main.app)
    assert c.post("/api/login", json={"name": name, "password": PW}, headers={"User-Agent": ua}).status_code == 200
    c.headers["User-Agent"] = ua
    return c


def _sid(c):
    return next(s["id"] for s in c.get("/api/me/sessions").json()["sessions"] if s["current"])


def _connect(c, player, name="Alice"):
    """What the event stream does at its start, without the open connection."""
    uid = main.db.find_user(name)["id"]
    label = main._own_session_label({"id": uid}, type("R", (), {"cookies": c.cookies})())
    return playerhub.connect(uid, player, _sid(c), *label)


def _state(c, player, playing=True, track=TRACK, **kw):
    return c.post("/api/player/state", json={"player": player, "playing": playing, "track": track, "position": 12.5, "duration": 200, "volume": 0.8, **kw})


def test_a_player_appears_for_the_other_devices(alice):
    phone, mac = _login(ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1"), _login()
    _connect(phone, "phone-0001")
    assert _state(phone, "phone-0001").json() == {"ok": True}
    (d,) = mac.get("/api/player/devices", params={"player": "mac-00001"}).json()["devices"]
    assert d["player"] == "phone-0001" and d["playing"] is True and d["track"]["title"] == "Song"
    assert (d["browser"], d["system"]) == ("Safari", "iPhone") and d["position"] == 12.5 and d["volume"] == 0.8


def test_the_asking_player_is_left_out_and_idle_ones_are_hidden(alice):
    c = _login()
    _connect(c, "tab-000001"); _connect(c, "tab-000002")
    _state(c, "tab-000001"); _state(c, "tab-000002", playing=False, track=None)  # nothing loaded
    assert c.get("/api/player/devices", params={"player": "tab-000001"}).json()["devices"] == []
    assert [d["player"] for d in c.get("/api/player/devices").json()["devices"]] == ["tab-000001"]


def test_playing_players_come_first(alice):
    c = _login()
    for p in ("tab-000001", "tab-000002"):
        _connect(c, p)
    _state(c, "tab-000001", playing=False)
    _state(c, "tab-000002", playing=True)
    assert [d["player"] for d in c.get("/api/player/devices").json()["devices"]] == ["tab-000002", "tab-000001"]


def test_a_player_that_is_not_connected_publishes_nothing(alice):
    c = _login()
    assert _state(c, "tab-000009").json() == {"ok": False}
    assert c.get("/api/player/devices").json()["devices"] == []


def test_state_that_stops_coming_no_longer_counts_as_playing(alice, monkeypatch):
    c = _login()
    _connect(c, "tab-000001"); _state(c, "tab-000001")
    monkeypatch.setattr(playerhub, "STATE_TTL", -1)
    assert c.get("/api/player/devices").json()["devices"][0]["playing"] is False


def test_a_command_reaches_the_target(alice):
    c = _login()
    target = _connect(c, "tab-000001")
    _drain(target)  # what it was told on connecting
    r = c.post("/api/player/command", json={"target": "tab-000001", "cmd": "seek", "value": 42})
    assert r.status_code == 200
    assert target.queue.get_nowait() == {"event": "command", "data": {"cmd": "seek", "value": 42}}
    assert c.post("/api/player/command", json={"target": "tab-000001", "cmd": "next"}).status_code == 200
    assert target.queue.get_nowait()["data"] == {"cmd": "next", "value": None}


def test_commands_are_checked(alice):
    c = _login()
    _connect(c, "tab-000001")
    post = lambda **kw: c.post("/api/player/command", json={"target": "tab-000001", **kw})  # noqa: E731
    assert post(cmd="dance").status_code == 422
    assert post(cmd="seek").status_code == 422 and post(cmd="volume").status_code == 422
    assert post(cmd="volume", value=1.5).status_code == 422
    assert post(cmd="volume", value=0.3).status_code == 200
    assert c.post("/api/player/command", json={"target": "nobody-0001", "cmd": "toggle"}).status_code == 404


def test_other_accounts_cannot_reach_a_player(alice, bob):
    a, b = _login("Alice"), _login("Bob")
    _connect(a, "tab-000001")
    _state(a, "tab-000001")
    assert b.get("/api/player/devices").json()["devices"] == []
    assert b.post("/api/player/command", json={"target": "tab-000001", "cmd": "pause"}).status_code == 404


def test_a_device_that_was_logged_out_gets_no_commands(alice):
    a, b = _login(), _login()
    _connect(b, "tab-000001")
    assert a.delete(f"/api/me/sessions/{_sid(b)}").status_code == 200
    assert a.post("/api/player/command", json={"target": "tab-000001", "cmd": "pause"}).status_code == 404


def test_a_slow_player_is_reported_busy(alice, monkeypatch):
    monkeypatch.setattr(playerhub, "QUEUE_SIZE", 2)
    c = _login()
    _drain(_connect(c, "tab-000001"))
    codes = [c.post("/api/player/command", json={"target": "tab-000001", "cmd": "toggle"}).status_code for _ in range(3)]
    assert codes == [200, 200, 409]


def test_a_reconnect_replaces_the_old_stream(alice):
    c = _login()
    first = _connect(c, "tab-000001")
    second = _connect(c, "tab-000001")
    assert None in _drain(first)  # the old stream is told to end
    uid = main.db.find_user("Alice")["id"]
    playerhub.disconnect(uid, "tab-000001", first)  # the old one closing must not remove the new one
    assert playerhub.session_of(uid, "tab-000001") == _sid(c) and second


def test_there_is_a_limit_of_players_per_account(alice, monkeypatch):
    monkeypatch.setattr(playerhub, "MAX_PLAYERS", 2)
    c = _login()
    assert _connect(c, "tab-000001") and _connect(c, "tab-000002")
    assert _connect(c, "tab-000003") is None
    assert _connect(c, "tab-000001")  # an existing one may reconnect


def test_the_state_is_validated(alice):
    c = _login()
    _connect(c, "tab-000001")
    assert _state(c, "x").status_code == 422
    assert _state(c, "tab-000001", volume=2).status_code == 422
    assert _state(c, "tab-000001", track={"id": "not an id", "title": "x"}).status_code == 422


def test_the_player_calls_need_a_login(anon):
    assert anon.get("/api/player/devices").status_code == 401
    assert anon.post("/api/player/command", json={"target": "tab-000001", "cmd": "play"}).status_code in (401, 403)
    assert anon.get("/api/player/events", params={"player": "tab-000001"}).status_code == 401


def test_the_event_stream_sends_commands_and_ends_with_the_login():
    """The stream itself, without a network: the first line, a command as an event, and the end when the login is gone."""
    async def run():
        q = asyncio.Queue()
        events = main._player_events(q, session=999999)  # no such login
        assert await events.__anext__() == ": connected\n\n"
        q.put_nowait({"event": "command", "data": {"cmd": "next", "value": None}})
        chunk = await events.__anext__()
        assert chunk.startswith("event: command\n") and json.loads(chunk.split("data: ")[1]) == {"cmd": "next", "value": None}
        q.put_nowait(None)
        assert [c async for c in events] == []
    asyncio.run(run())


def _drain(p):
    out = []
    while not p.queue.empty():
        out.append(p.queue.get_nowait())
    return out


def _devices_events(p):
    return [m["data"] for m in _drain(p) if m and m["event"] == "devices"]


def test_the_other_devices_hear_when_a_player_starts_and_stops(alice):
    c = _login()
    mac, phone = _connect(c, "mac-000001"), _connect(c, "phone-00001")
    _drain(mac), _drain(phone)
    _state(c, "mac-000001")
    heard = _devices_events(phone)
    assert [d["player"] for d in heard[-1]] == ["mac-000001"] and heard[-1][0]["playing"] is True
    assert _devices_events(mac) == []  # the player itself is not told about itself
    _state(c, "mac-000001", playing=False)
    assert _devices_events(phone)[-1][0]["playing"] is False
    uid = main.db.find_user("Alice")["id"]
    playerhub.disconnect(uid, "mac-000001", mac)  # the tab was closed
    assert _devices_events(phone)[-1] == []


def test_a_new_connection_is_told_who_plays(alice):
    c = _login()
    mac = _connect(c, "mac-000001")
    _state(c, "mac-000001")
    phone = _connect(c, "phone-00001")
    assert [d["player"] for d in _devices_events(phone)[-1]] == ["mac-000001"]
    assert mac.queue.empty() or all(m["event"] == "devices" for m in _drain(mac))


def test_only_real_changes_are_pushed(alice):
    c = _login()
    mac, phone = _connect(c, "mac-000001"), _connect(c, "phone-00001")
    _state(c, "mac-000001")
    _drain(phone)
    for pos in (30, 60, 90):  # the position ticks on: nothing to tell
        c.post("/api/player/state", json={"player": "mac-000001", "playing": True, "track": TRACK, "position": pos, "duration": 200, "volume": 0.8})
    assert _devices_events(phone) == []
    _state(c, "mac-000001", track={**TRACK, "id": "test:00000002"})  # another song: now there is news
    assert len(_devices_events(phone)) == 1


def test_a_player_that_stopped_reporting_is_noticed_by_the_sweep(alice, monkeypatch):
    c = _login()
    _connect(c, "mac-000001"); phone = _connect(c, "phone-00001")
    _state(c, "mac-000001")
    _drain(phone)
    monkeypatch.setattr(playerhub, "STATE_TTL", -1)
    uid = main.db.find_user("Alice")["id"]
    playerhub.refresh(uid, "phone-00001")  # what the stream does every 20 s
    assert _devices_events(phone)[-1][0]["playing"] is False


def test_the_stream_sweeps_for_its_player():
    """The ping interval also refreshes the device list; here without a network and with a login that is gone."""
    async def run():
        q = asyncio.Queue()
        events = main._player_events(q, session=999999, user_id=1, player="x" * 10)
        assert await events.__anext__() == ": connected\n\n"
        q.put_nowait(None)
        assert [c async for c in events] == []
    asyncio.run(run())
