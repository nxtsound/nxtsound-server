# Demo instance and screenshots

Scripts that build a throw-away demo server (made-up artists, generated tones and covers, nothing real) and take the README screenshots from it,
once at desktop size and once at phone size. Run them from the repository root; everything is created in `tools/demo/work/` (ignored by git).

```sh
pip install pillow playwright mutagen numpy        # tools only, not needed to run the server
python tools/demo/gen.py tools/demo/work/music     # 37 tiny audio files with tags and cover art
python tools/demo/seed.py                          # users, history, likes, playlists, friends
python tools/demo/run_demo.py &                    # the demo server on http://localhost:8030 (log in as `demo`; the random password is in `tools/demo/work/demo-password.txt`)
python tools/demo/shots.py docs/screenshots home,player,search,playlists,recap,community
```

For the plugin screenshots install the plugins first (Admin area → Plugins, or from a local checkout with `run_demo.py`, which allows local folders),
then `python tools/demo/shots.py docs/screenshots admin-plugins,plugin-settings`. If you installed from local folders, set `DEMO_LOCAL_REPOS=/path/to/checkouts/`
so the pages show `https://github.com/nxtsound/…` instead of your paths. Chrome must be installed (Playwright uses it).

Environment: `DEMO_DIR` (work folder), `DEMO_PORT` (default 8030), `DEMO_URL` (where `shots.py` looks).
`seed.py` generates a new random password for every demo database and writes it to `demo-password.txt` (set `DEMO_PASSWORD` to override it for `shots.py`).
