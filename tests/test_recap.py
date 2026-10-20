"""The recap: numbers about what a user listened to over a period."""
import time

import db
from conftest import make_client

DAY = 86400


def track(n: int, artist: str = "Band A", source: str = "local") -> dict:
    return {"id": f"{source}:t{n}", "title": f"Song {n}", "artist": artist, "artist_id": f"a-{artist}", "album": "Album", "album_id": "al", "duration": "3:00", "thumb": ""}


def play(user_id: int, t: dict, days_ago: float, hour: int | None = None) -> None:
    ts = time.time() - days_ago * DAY
    if hour is not None:  # a play at a given UTC hour on that day
        g = time.gmtime(ts)
        ts = time.mktime((g.tm_year, g.tm_mon, g.tm_mday, hour, 30, 0, 0, 0, 0)) - time.timezone
    with db.db() as c:
        c.execute(f"INSERT INTO plays(user_id, {db._INSERT_COLS}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (user_id, *db._track_values(t), int(ts)))


def listen(user_id: int, days_ago: int, seconds: float) -> None:
    with db.db() as c:
        c.execute("INSERT OR REPLACE INTO listening(user_id, day, seconds) VALUES(?, ?, ?)", (user_id, db._day(time.time() - days_ago * DAY), seconds))


def test_an_empty_recap_is_all_zeros(alice):
    r = alice.get("/api/me/recap").json()
    assert (r["seconds"], r["plays"], r["songs"], r["artists"], r["active_days"]) == (0, 0, 0, 0, 0)
    assert r["top_songs"] == [] and r["top_artists"] == [] and r["best_day"] is None and r["streak"] == {"current": 0, "longest": 0} and r["weekdays"] == [0] * 7


def test_totals_and_tops(alice):
    uid = db.find_user("Alice")["id"]
    for _ in range(3):
        play(uid, track(1), 1)
    play(uid, track(2), 2)
    play(uid, track(3, "Band B"), 3)
    play(uid, track(4, "Band B", "demo"), 3)
    listen(uid, 1, 3600)
    listen(uid, 2, 1800)
    r = alice.get("/api/me/recap", params={"period": "week"}).json()
    assert (r["plays"], r["songs"], r["artists"], r["seconds"], r["active_days"]) == (6, 4, 2, 5400, 2)
    assert [(s["title"], s["plays"]) for s in r["top_songs"]][:1] == [("Song 1", 3)]
    assert [(a["name"], a["plays"]) for a in r["top_artists"]] == [("Band A", 4), ("Band B", 2)]
    assert r["best_day"]["seconds"] == 3600 and r["average_seconds"] == 2700
    assert {s["source"]: s["plays"] for s in r["sources"]} == {"local": 5, "demo": 1}


def test_periods_cut_off_older_plays(alice):
    uid = db.find_user("Alice")["id"]
    play(uid, track(1), 2)
    play(uid, track(2), 20)
    play(uid, track(3), 200)
    plays = lambda p: alice.get("/api/me/recap", params={"period": p}).json()["plays"]  # noqa: E731
    assert (plays("week"), plays("month"), plays("year"), plays("all")) == (1, 2, 3, 3)
    assert alice.get("/api/me/recap", params={"period": "decade"}).status_code == 422


def test_comparison_with_the_period_before(alice):
    uid = db.find_user("Alice")["id"]
    for i in range(3):
        play(uid, track(i), 2)
    play(uid, track(9), 10)  # in the 7 days before the last 7
    listen(uid, 2, 600)
    listen(uid, 10, 300)
    r = alice.get("/api/me/recap", params={"period": "week"}).json()
    assert r["previous"] == {"plays": 1, "seconds": 300} and r["plays"] == 3
    assert alice.get("/api/me/recap", params={"period": "all"}).json()["previous"] is None


def test_new_artists_are_those_not_heard_before_the_period(alice):
    uid = db.find_user("Alice")["id"]
    play(uid, track(1, "Old Band"), 100)
    play(uid, track(2, "Old Band"), 2)
    play(uid, track(3, "Fresh Band"), 2)
    r = alice.get("/api/me/recap", params={"period": "week"}).json()
    assert r["new_artists"] == {"count": 1, "names": ["Fresh Band"]}
    assert alice.get("/api/me/recap", params={"period": "all"}).json()["new_artists"]["count"] == 2


def test_weekdays_and_hours_follow_the_users_time_zone(alice):
    uid = db.find_user("Alice")["id"]
    play(uid, track(1), 1, hour=23)  # 23:30 UTC
    utc = alice.get("/api/me/recap", params={"period": "week"}).json()
    east = alice.get("/api/me/recap", params={"period": "week", "tz": 120}).json()  # two hours ahead: 01:30 the next day
    assert utc["hours"][23] == 1 and east["hours"][1] == 1 and east["hours"][23] == 0
    assert utc["weekdays"].index(1) == (east["weekdays"].index(1) - 1) % 7
    assert alice.get("/api/me/recap", params={"tz": 9999}).status_code == 422


def test_streaks():
    day = lambda n: db._day(time.time() - n * DAY)  # noqa: E731
    assert db._streaks(set()) == (0, 0)
    assert db._streaks({day(0), day(1), day(2), day(10), day(11)}) == (3, 3)  # ends today
    assert db._streaks({day(1), day(2), day(5)}) == (2, 2)  # yesterday still counts
    assert db._streaks({day(3), day(4), day(5), day(6)}) == (0, 4)  # broken: no current streak, the longest stays


def test_everyone_sees_only_their_own_recap(alice, bob):
    play(db.find_user("Alice")["id"], track(1), 1)
    assert alice.get("/api/me/recap").json()["plays"] == 1 and bob.get("/api/me/recap").json()["plays"] == 0


def test_recap_needs_login(anon):
    assert anon.get("/api/me/recap").status_code == 401
