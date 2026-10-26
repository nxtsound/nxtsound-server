"""Developer tools: a snapshot of the data that radio, mixes and the home page are made from, with every name replaced.

Meant for bug reports: "the radio gives me rubbish" can be attached as a file, and someone else loads it into a development instance
and sees the same candidates and scores. What is kept is what the logic looks at: which songs share an artist, an album, a genre or a year, how
often and in which order the signed-in user played and liked songs, their dislikes and the tempo/key analysis. What is replaced: titles,
artists, albums (by "Song 12", "Artist 3", "Album 5"), the IDs, the paths. Covers, lyrics, other users and settings are not part of it.
Genres and years stay as they are (the logic needs them); look at them before you attach a snapshot to a public issue.
"""
import json

import db
import library

FORMAT = 1
NOTE = ("Names, IDs and paths are replaced. Kept as they are: genre, year, duration, track numbers, when songs were added, "
        "the plays/likes/dislikes of one user (with their times) and the tempo/key analysis.")


class _Names:
    """Value -> "<label> <n>", numbered in the order the values are first asked for (so the result does not depend on the original names)."""

    def __init__(self, label: str):
        self.label, self.map = label, {}

    def __call__(self, value: str) -> str:
        if not value:
            return ""
        if value not in self.map:
            self.map[value] = f"{self.label} {len(self.map) + 1}"
        return self.map[value]


class _Ids:
    def __init__(self, prefix: str):
        self.prefix, self.map = prefix, {}

    def __call__(self, value: str) -> str:
        if not value:
            return ""
        if value not in self.map:
            self.map[value] = f"{self.prefix}{len(self.map) + 1:05d}"
        return self.map[value]


def export(user_id: int, mapping: dict | None = None) -> dict:
    """The snapshot of one user's view. `mapping` (tests only) receives original ID -> snapshot ID; it is never part of the result."""
    song, artist, album = _Names("Song"), _Names("Artist"), _Names("Album")
    track_id, artist_id, album_id = _Ids("local:snap-"), _Ids("snapartist"), _Ids("snapalbum")
    with db.db() as c:
        lib_rows = c.execute("SELECT * FROM lib_tracks ORDER BY rowid").fetchall()
        plays = c.execute("SELECT * FROM plays WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        likes = c.execute("SELECT * FROM likes WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        dislikes = c.execute("SELECT kind, ref, title, ts FROM dislikes WHERE user_id = ? ORDER BY ts", (user_id,)).fetchall()
        bpm = {r["video_id"]: r for r in c.execute("SELECT video_id, bpm, first_beat, conf, extra FROM bpm")}
    other = _Ids("snap:")  # songs that are not in the library (a plugin's): only needed so that plays and likes keep their meaning
    tid = {}

    def new_id(old: str) -> str:
        if old not in tid:
            tid[old] = track_id(old) if old in lib_ids else other(old)
        return tid[old]

    lib_ids = {r["id"] for r in lib_rows}
    tracks = []
    for r in lib_rows:
        tracks.append({
            "id": new_id(r["id"]), "title": song(r["id"]), "artist": artist(r["artist"]), "album": album(r["album_id"] or r["album"]) if r["album"] else "",
            "album_artist": artist(r["album_artist"]), "artist_id": artist_id(r["artist_id"]), "album_id": album_id(r["album_id"]),
            "track_no": r["track_no"], "disc_no": r["disc_no"], "year": r["year"], "genre": r["genre"], "duration": r["duration"], "added": r["added"],
        })

    def play_row(r, when_key):
        old = r["video_id"]
        return {"id": new_id(old), "title": song(old), "artist": artist(r["artist"]), "artist_id": artist_id(r["artist_id"]),
                "album": album(r["album_id"] or r["album"]) if r["album"] else "", "album_id": album_id(r["album_id"]),
                "duration": r["duration"], when_key: r[when_key]}

    out = {
        "format": FORMAT, "note": NOTE,
        "tracks": tracks,
        "plays": [play_row(r, "played_at") for r in plays],
        "likes": [play_row(r, "liked_at") for r in likes],
        "dislikes": [{"kind": d["kind"], "ref": (new_id(d["ref"]) if d["kind"] == "track" else artist_id(d["ref"])), "title": "", "ts": d["ts"]} for d in dislikes],
        "bpm": [{"id": tid[i], "bpm": r["bpm"], "first_beat": r["first_beat"], "conf": r["conf"], "extra": json.loads(r["extra"]) if r["extra"] else {}}
                for i, r in bpm.items() if i in tid and i in lib_ids],
    }
    if mapping is not None:
        mapping.update(tid)
    return out


def load(data: dict, user_name: str = "snapshot", password: str = "") -> dict:
    """Puts a snapshot into the current database (a development instance) as songs without audio files, and creates a user with the plays,
    likes and dislikes. Returns {"user": ..., "tracks": n}."""
    if data.get("format") != FORMAT:
        raise ValueError(f"unknown snapshot format {data.get('format')!r}")
    user = db.create_user(user_name, password)
    if user is None:
        raise ValueError(f"the user {user_name!r} exists already")
    uid = user["id"]
    with db.db() as c:
        for n, t in enumerate(data["tracks"], 1):
            c.execute(
                "INSERT OR REPLACE INTO lib_tracks(id, path, title, artist, album, album_artist, artist_id, album_id, track_no, disc_no, year, genre, "
                "duration, size, mtime, cover, search, added) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', ?, ?)",
                (t["id"], f"snapshot/{n:05d}.mp3", t["title"], t["artist"], t["album"], t["album_artist"], t["artist_id"], t["album_id"], t["track_no"],
                 t["disc_no"], t["year"], t["genre"], t["duration"], library._norm(f"{t['title']} {t['artist']} {t['album']} {t['album_artist']}"), t["added"]))
        cols = "video_id, title, artist, artist_id, album, album_id, duration, thumb"
        for p in data["plays"]:
            c.execute(f"INSERT INTO plays(user_id, {cols}, played_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
                      (uid, p["id"], p["title"], p["artist"], p["artist_id"], p["album"], p["album_id"], p["duration"], p["played_at"]))
        for p in data["likes"]:
            c.execute(f"INSERT OR IGNORE INTO likes(user_id, {cols}, liked_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
                      (uid, p["id"], p["title"], p["artist"], p["artist_id"], p["album"], p["album_id"], p["duration"], p["liked_at"]))
        for d in data["dislikes"]:
            c.execute("INSERT OR REPLACE INTO dislikes(user_id, kind, ref, title, ts) VALUES(?, ?, ?, ?, ?)", (uid, d["kind"], d["ref"], d["title"], d["ts"]))
    for b in data["bpm"]:
        db.save_bpm(b["id"], {"bpm": b["bpm"], "first_beat": b["first_beat"], "conf": b["conf"], **b["extra"]})
    return {"user": user_name, "tracks": len(data["tracks"])}
