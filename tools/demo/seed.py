"""Fills a demo database: users, 75 days of listening history, likes, playlists, friends. Run gen.py first (see README.md)."""
import os, random, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
D = os.environ.get("DEMO_DIR", str(HERE / "work"))
os.environ.update(DB_PATH=f"{D}/data/server.db", COVER_DIR=f"{D}/data/covers", MUSIC_DIR=f"{D}/music", PLUGINS_DIR=f"{D}/plugins",
                  PLUGIN_DATA_DIR=f"{D}/data/plugin-data", ANALYZE="0", ENRICH="0", SYSMON_ON_START="0", PLUGIN_USE_VENV="0", LYRICS="0")
sys.path.insert(0, str(HERE.parents[1]))
import db, library
os.makedirs(f"{D}/data", exist_ok=True)
db.init_db()
rnd = random.Random(11)
for name, admin in (("demo", True), ("maria", False), ("jonas", False), ("lena", False)):
    if not db.find_user(name):
        db.create_user(name, "demo-pass-1234", admin)
uid = {n: db.find_user(n)["id"] for n in ("demo", "maria", "jonas", "lena")}
with db.db() as c:
    c.execute("UPDATE users SET onboarded = 1, news_seen = '0.1.0'")
library.scan(full=True)
tracks = library.recently_added(200)
by_artist = {}
for t in tracks: by_artist.setdefault(t["artist"], []).append(t)
print("tracks", len(tracks), {a: len(v) for a, v in by_artist.items()})
fav = by_artist["Neon Harbor"] + by_artist["Mira Solis"] * 1 + by_artist["Velvet Static"][:3]
rest = [t for t in tracks if t not in fav]
now = time.time()

def plays_for(user, count, days, weights):
    rows = []
    for _ in range(count):
        t = rnd.choices(*weights)[0]
        age = rnd.random() ** 1.4 * days * 86400
        hour = rnd.choices(range(24), [1,1,1,1,1,1,2,3,3,2,2,3,4,4,3,3,4,5,8,9,10,9,6,3])[0]
        ts = now - age
        g = time.gmtime(ts)
        ts = time.mktime((g.tm_year, g.tm_mon, g.tm_mday, hour, rnd.randint(0, 59), 0, 0, 0, 0)) - time.timezone
        rows.append((min(ts, now - 90 - rnd.randint(0, 3000)), t))
    for ts, t in sorted(rows, key=lambda r: r[0]):
        with db.db() as c:
            c.execute(f"INSERT INTO plays(user_id, {db._INSERT_COLS}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (user, *db._track_values(t), int(ts)))
    return rows

pool = fav + rest
w = [4 if t in fav else 1 for t in pool]
rows = plays_for(uid["demo"], 190, 75, (pool, w))
# listening time per day (seconds), a streak of the last 9 days
with db.db() as c:
    days = {}
    for ts, t in rows:
        d = time.strftime("%Y-%m-%d", time.gmtime(ts)); days[d] = days.get(d, 0) + 200
    for k in range(9):
        d = time.strftime("%Y-%m-%d", time.gmtime(now - k * 86400)); days[d] = max(days.get(d, 0), 1800 + rnd.randint(0, 3000))
    for d, s in days.items():
        c.execute("INSERT OR REPLACE INTO listening(user_id, day, seconds) VALUES(?, ?, ?)", (uid["demo"], d, s))
# the very latest plays of the demo user: varied
recent = [by_artist["Mira Solis"][0], by_artist["Neon Harbor"][1], by_artist["Orbit Kids"][2], by_artist["Paper Lanterns"][0], by_artist["The Quiet Engines"][1], by_artist["Velvet Static"][0], by_artist["Neon Harbor"][0]]
for i, t in enumerate(recent):
    with db.db() as c:
        c.execute(f"INSERT INTO plays(user_id, {db._INSERT_COLS}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (uid["demo"], *db._track_values(t), int(now - 3600 * (len(recent) - i) * 2)))
for t in rnd.sample(fav, 11): db.like_track(uid["demo"], t)
for name, picks in (("Late night drive", by_artist["Neon Harbor"][:6] + by_artist["Orbit Kids"][:2]), ("Sunday morning", by_artist["Paper Lanterns"] + by_artist["Mira Solis"][:3]), ("Focus", by_artist["Orbit Kids"] + by_artist["Velvet Static"][:3])):
    pid = db.create_playlist(uid["demo"], name); db.add_tracks(uid["demo"], pid, picks)
# friends
db.set_community_enabled(True)
for friend, count, pool_ in (("maria", 60, by_artist["Mira Solis"] + by_artist["Paper Lanterns"] + by_artist["Velvet Static"]), ("jonas", 60, by_artist["The Quiet Engines"] + by_artist["Orbit Kids"] + by_artist["Neon Harbor"])):
    db.set_share(uid[friend], True)
    plays_for(uid[friend], count, 5, (pool_, [1] * len(pool_)))
    db.follow(uid["demo"], uid[friend]); db.follow(uid[friend], uid["demo"])
with db.db() as c:  # one friend is listening right now
    t = by_artist["Mira Solis"][1]
    c.execute(f"INSERT INTO plays(user_id, {db._INSERT_COLS}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (uid["maria"], *db._track_values(t), int(now - 45)))
db.set_share(uid["demo"], True); db.follow(uid["lena"], uid["demo"])
db.notify(uid["demo"], f"follow:{uid['lena']}", "follow", {"user_id": uid["lena"], "name": "lena"})
print("seeded")
