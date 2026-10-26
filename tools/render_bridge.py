"""Makes a DJ bridge from two audio files, to listen to what the server would play (see djmix.py).

    python tools/render_bridge.py old.mp3 new.mp3 bridge.m4a                  random style
    python tools/render_bridge.py old.mp3 new.mp3 bridge.m4a --style filter   blend, filter, echo, loop or bass

The bridge starts with the last seconds before the transition (the old song as it is), then the transition, then the new song. It prints what it decided.
Needs numpy and ffmpeg, like the server.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpm  # noqa: E402
import djmix  # noqa: E402


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--style", default="auto", choices=["auto", *djmix.STYLES])
    args = ap.parse_args()
    if not djmix.available():
        print("numpy is needed (pip install numpy), and ffmpeg")
        return 1
    print("Analyzing …")
    a, b = bpm.analyze(args.old), bpm.analyze(args.new)
    if not a or not b:
        print("No tempo found in", "the old song" if not a else "the new song")
        return 1
    p = djmix.plan(a, b, duration(args.old), args.style)
    if p["skip"]:
        print("No transition:", p["why"])
        return 1
    print("Mixing …")
    found = djmix.render(args.old, args.new, p, args.out)
    print(json.dumps({**{k: p[k] for k in ("style", "lock", "rate", "bpm_a", "bpm_b", "t_a", "mix_sec", "x", "info")}, **found}, indent=1))
    print(f"\nWritten: {args.out}. It starts {p['a_start']:.1f} s into the old song and the transition begins {djmix.LEAD:.0f} s in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
