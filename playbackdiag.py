"""Turns the app's debug-mode snapshots (db.playback_diag) into something a human can scan on the admin area's Playback page: grouped by
user, then device, then "session" (a run with no gap of SESSION_GAP seconds or more - the app was backgrounded, relaunched, or debug mode
was switched off and on), each with a quick green/yellow/red verdict and a mobile/Wi-Fi label, and a listener's own "something is wrong"
reports (db.playback_reports) matched in by time, not by device (the report and the notes it is about rarely share one exact device label:
see devices.py).

Nothing here talks to the database or the web; main.py passes it what db.py read and gets back what the page renders.
"""
SESSION_GAP = 300    # seconds: a gap this long starts a new session
REPORT_MARGIN = 120  # seconds: a report counts as "about" a session if it falls within this far of its start or end
RETRIES_RED = 5      # a single snapshot already fighting this hard to reconnect makes the whole session red on its own


def _verdict(entries: list[dict]) -> str:
    """green/yellow/red: a quick read of how rough a session was, from the snapshots' own numbers alone."""
    if any(e["item_status"] == "failed" or e["retries"] >= RETRIES_RED for e in entries):
        return "red"
    if any(e["retries"] > 0 or e["net_status"] == "unsatisfied" for e in entries):
        return "yellow"
    return "green"


def _connection(entries: list[dict]) -> str:
    expensive = sum(1 for e in entries if e["net_expensive"])
    if expensive == 0:
        return "wifi"
    if expensive == len(entries):
        return "mobile"
    return "mixed"


def _sessions(entries: list[dict]) -> list[list[dict]]:
    """`entries`: one device's snapshots, newest first. -> sessions newest first, each newest-first inside."""
    ascending = list(reversed(entries))
    runs: list[list[dict]] = []
    for e in ascending:
        if runs and e["ts"] - runs[-1][-1]["ts"] <= SESSION_GAP:
            runs[-1].append(e)
        else:
            runs.append([e])
    runs.reverse()
    return [list(reversed(r)) for r in runs]


def _reports_near(user_reports: list[dict], start: int, end: int) -> list[dict]:
    return [r for r in user_reports if start - REPORT_MARGIN <= r["ts"] <= end + REPORT_MARGIN]


def group(entries: list[dict], reports: list[dict]) -> list[dict]:
    """`entries`/`reports`: any order (sorted here by `ts`, so a batch uploaded late - e.g. after a dead zone - still lands where it
    belongs instead of wherever it happened to be inserted).
    -> [{user, devices: [{device, sessions: [{start, end, count, connection, verdict, reports, entries}]}]}], users with the most
    recent snapshot first (so whoever has something fresh to look at floats to the top)."""
    by_user: dict[str, dict[str, list[dict]]] = {}
    for e in entries:
        dev = e["device"] or "Unknown device"
        by_user.setdefault(e["user_name"], {}).setdefault(dev, []).append(e)
    reports_by_user: dict[str, list[dict]] = {}
    for r in reports:
        reports_by_user.setdefault(r["user_name"], []).append(r)

    out = []
    for user, by_device in by_user.items():
        devices_out = []
        newest = 0
        for dev, dev_entries in by_device.items():
            dev_entries.sort(key=lambda e: e["ts"], reverse=True)
            newest = max(newest, dev_entries[0]["ts"])
            sessions_out = []
            for session in _sessions(dev_entries):
                start, end = session[-1]["ts"], session[0]["ts"]
                sessions_out.append({
                    "start": start, "end": end, "count": len(session),
                    "connection": _connection(session), "verdict": _verdict(session),
                    "reports": _reports_near(reports_by_user.get(user, []), start, end),
                    "entries": session,
                })
            devices_out.append({"device": dev, "sessions": sessions_out})
        out.append((newest, {"user": user, "devices": devices_out}))
    out.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in out]
