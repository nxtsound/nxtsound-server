"""Loads a snapshot from the developer tools into a development instance, so that you see what the person who attached it saw.

    DB_PATH=./dev/server.db python tools/load_snapshot.py nxtsound-snapshot.json
    DB_PATH=./dev/server.db python tools/load_snapshot.py nxtsound-snapshot.json --user dev --password choose-one

The songs have no audio files (the data is all the radio, the mixes and the home page need). Sign in as the created user and look at the radio,
the home page and the "For you" lists; with DEVTOOLS=1 and an admin account you see the scores. Use a database of your own for this, not one with data.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import devsnapshot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--user", default="snapshot", help="name of the user that gets the plays, likes and dislikes (default: snapshot)")
    ap.add_argument("--password", default="", help="password of that user (without one nobody can sign in)")
    args = ap.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8"))
    db.init_db()
    info = devsnapshot.load(data, args.user, args.password)
    print(f"Loaded {info['tracks']} songs, {len(data['plays'])} plays, {len(data['likes'])} likes for the user {info['user']!r} into {db.DB_PATH}.")
    if not args.password:
        print("No password given, so nobody can sign in as this user: run again against a new database with --password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
