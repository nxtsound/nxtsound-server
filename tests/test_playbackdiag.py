"""The app's debug mode end to end: ingestion (`/api/diag/playback`), a listener's own "something is wrong" report
(`/api/diag/report`), and turning both into the grouped, judged view the admin area's Playback page shows (playbackdiag.py)."""
import devtools
import main
import playbackdiag
from conftest import make_client


def _entry(**over):
    e = {"ts": 1700000000, "track_id": "local:x", "t": 12.5, "want_play": True, "time_control": "waiting", "item_status": "unknown",
         "item_error": "", "retries": 0, "net_status": "satisfied", "net_expensive": False, "app_state": "background", "platform": "ios"}
    e.update(over)
    return e


# ---------- Ingestion ----------

def test_playback_diag_is_only_kept_with_devtools_on(alice, lib, monkeypatch):
    bob = make_client("Bob")
    assert bob.post("/api/diag/playback", json={"entries": [_entry()]}).json() == {"ok": True}
    assert "playback_diag" not in alice.get("/api/server-info").json()["functions"]
    assert alice.get("/api/admin/playback").json() == {"available": False}
    assert main.db.playback_diag() == []  # DEVTOOLS is off: nothing was stored

    monkeypatch.setattr(devtools, "ENABLED", True)
    assert "playback_diag" in alice.get("/api/server-info").json()["functions"]
    bob.post("/api/diag/playback", json={"entries": [_entry(track_id="local:y", retries=5)]},
             headers={"User-Agent": "NxtSound-iOS/1 (iPhone; iOS 18.1)"})
    rows = main.db.playback_diag()
    assert len(rows) == 1 and rows[0]["track_id"] == "local:y" and rows[0]["retries"] == 5 and rows[0]["user_name"] == "Bob"
    assert rows[0]["device"] == "NxtSound-iOS on iPhone"  # grouped by this on the admin page (user -> device -> session)


def test_report_is_only_kept_with_devtools_on_and_labels_its_platform(alice, lib, monkeypatch):
    bob = make_client("Bob")
    bob.post("/api/diag/report", json={"track_id": "local:x", "reason": "stopped on the lock screen"})
    assert main.db.playback_reports() == []  # DEVTOOLS is off

    monkeypatch.setattr(devtools, "ENABLED", True)
    bob.post("/api/diag/report", json={"track_id": "local:x", "reason": "stopped on the lock screen"},
             headers={"User-Agent": "NxtSound-iOS/1 (iPhone; iOS 18.1)"})
    bob.post("/api/diag/report", json={"reason": "crackling"}, headers={"User-Agent": "Mozilla/5.0 Safari/605"})
    rows = main.db.playback_reports()
    assert len(rows) == 2 and {r["platform"] for r in rows} == {"ios", "web"}
    assert {r["reason"] for r in rows} == {"stopped on the lock screen", "crackling"}


def test_admin_playback_is_for_admins_only(alice, lib, monkeypatch):
    monkeypatch.setattr(devtools, "ENABLED", True)
    assert make_client("Bob").get("/api/admin/playback").status_code == 403
    assert alice.get("/api/admin/playback").json()["available"] is True


# ---------- Grouping and the traffic-light verdict (playbackdiag.py; no DB, no server needed) ----------

def _row(user="Alice", device="iPhone", **over):
    base = {"user_name": user, "device": device, "ts": 1_700_000_000, "track_id": "", "t": 0.0, "want_play": True,
            "time_control": "playing", "item_status": "ready", "item_error": "", "retries": 0, "net_status": "satisfied",
            "net_expensive": False, "app_state": "background", "platform": "ios"}
    base.update(over)
    return base


def test_a_clean_run_is_green_and_splits_into_sessions_on_a_big_gap():
    entries = [_row(ts=1000), _row(ts=1010), _row(ts=1010 + playbackdiag.SESSION_GAP + 1), _row(ts=1010 + playbackdiag.SESSION_GAP + 20)]
    out = playbackdiag.group(list(reversed(entries)), [])  # newest first, like db.playback_diag returns
    [user] = out
    [device] = user["devices"]
    assert len(device["sessions"]) == 2  # the gap split it in two
    newest, oldest = device["sessions"]
    assert newest["start"] < newest["end"] and newest["count"] == 2 and newest["verdict"] == "green" and newest["connection"] == "wifi"
    assert oldest["start"] <= oldest["end"]


def test_retries_and_failures_turn_the_verdict_yellow_then_red():
    yellow = playbackdiag.group(list(reversed([_row(ts=1), _row(ts=2, retries=1)])), [])
    assert yellow[0]["devices"][0]["sessions"][0]["verdict"] == "yellow"
    red_by_failure = playbackdiag.group(list(reversed([_row(ts=1), _row(ts=2, item_status="failed")])), [])
    assert red_by_failure[0]["devices"][0]["sessions"][0]["verdict"] == "red"
    red_by_retries = playbackdiag.group([_row(ts=1, retries=playbackdiag.RETRIES_RED)], [])
    assert red_by_retries[0]["devices"][0]["sessions"][0]["verdict"] == "red"


def test_grouping_sorts_by_ts_itself_even_if_a_late_batch_was_inserted_out_of_order():
    """A device buffers notes through a dead zone and uploads them once it is back: the server stores that old batch after newer ones
    that reached it first. Grouping must not mistake insertion order for chronological order, or it would merge unrelated sessions."""
    recent = [_row(ts=100_000 + i * 10) for i in range(3)]              # just happened
    old_dead_zone = [_row(ts=i * 10) for i in range(3)]                 # uploaded late, but from long before `recent`
    entries = recent + old_dead_zone                                   # inserted (and so returned by the DB) in this order
    out = playbackdiag.group(entries, [])
    [device] = out[0]["devices"]
    assert len(device["sessions"]) == 2  # not merged into one just because they arrived back to back
    newest, oldest = device["sessions"]
    assert newest["start"] == 100_000 and oldest["start"] == 0


def test_connection_label_reflects_how_much_of_the_session_was_mobile():
    mobile = playbackdiag.group([_row(ts=1, net_expensive=True)], [])
    assert mobile[0]["devices"][0]["sessions"][0]["connection"] == "mobile"
    mixed = playbackdiag.group(list(reversed([_row(ts=1, net_expensive=True), _row(ts=2, net_expensive=False)])), [])
    assert mixed[0]["devices"][0]["sessions"][0]["connection"] == "mixed"


def test_a_report_is_matched_to_the_session_it_falls_near_regardless_of_device():
    entries = [_row(user="Alice", device="NxtSound-iOS on iPhone", ts=1000)]
    near = {"user_name": "Alice", "ts": 1000 + playbackdiag.REPORT_MARGIN - 1, "reason": "stopped", "platform": "web"}
    far = {"user_name": "Alice", "ts": 1000 + playbackdiag.REPORT_MARGIN + 999, "reason": "unrelated", "platform": "web"}
    other_user = {"user_name": "Bob", "ts": 1000, "reason": "not yours", "platform": "web"}
    out = playbackdiag.group(entries, [near, far, other_user])
    reports = out[0]["devices"][0]["sessions"][0]["reports"]
    assert [r["reason"] for r in reports] == ["stopped"]  # matched by time and user only - device labels need not match


def test_users_come_in_order_of_their_newest_snapshot():
    entries = [_row(user="Older", ts=1), _row(user="Newer", ts=2)]  # db.playback_diag would give newest first
    out = playbackdiag.group(list(reversed(entries)), [])
    assert [u["user"] for u in out] == ["Newer", "Older"]
