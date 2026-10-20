<div align="center">

# NxtSound

**Your music. Your server. No subscription, no tracking.**

A self-hosted music server with a fast web app, smart recommendations and a plugin system.
Point it at a folder of music files and it feels like a streaming service that belongs to you.

[![CI](https://github.com/nxtsound/nxtsound-server/actions/workflows/ci.yml/badge.svg)](https://github.com/nxtsound/nxtsound-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange)

<img src="docs/screenshots/home.svg" alt="Home page" width="55%">

[Quick start](#quick-start) · [Why](#why-nxtsound) · [Features](#features) · [Plugins](#plugins) · [Roadmap](#roadmap) · [Support](#support-the-project) · [Contribute](#contributing) · [Docs](#documentation)

</div>

## Quick start

You need Docker and a folder with music (MP3, FLAC, M4A/AAC, OGG, Opus, WAV, AIFF). Change the two marked lines, paste, done:

```sh
mkdir nxtsound && cd nxtsound
cat > docker-compose.yml <<'EOF'
services:
  nxtsound:
    image: ghcr.io/nxtsound/nxtsound-server:latest
    restart: unless-stopped
    ports: ["8000:8000"]
    environment:
      ADMIN_USER: admin
      ADMIN_PASSWORD: change-me-please                  # <- choose a password (8+ characters)
    volumes:
      - nxtsound-data:/data
      - /path/to/your/music:/music:ro                   # <- your music folder (read-only)
      - ./plugins:/plugins
volumes:
  nxtsound-data:
EOF
docker compose up -d
```

Open <http://localhost:8000>, log in, and give the first scan a moment. That is it.

<details>
<summary><b>No Docker?</b> Run it directly (Python 3.12)</summary>

```sh
git clone https://github.com/nxtsound/nxtsound-server.git && cd nxtsound-server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ADMIN_USER=admin ADMIN_PASSWORD=change-me-please MUSIC_DIR=/path/to/your/music \
  .venv/bin/uvicorn main:app --port 8000
```

`ffmpeg` is optional (DJ analysis), `git` is needed to install plugins from repositories.
</details>

<details>
<summary><b>Prefer building from source with Docker?</b></summary>

```sh
git clone https://github.com/nxtsound/nxtsound-server.git && cd nxtsound-server
cp env.example .env        # set MUSIC_PATH, ADMIN_USER and ADMIN_PASSWORD
docker compose up -d --build
```
</details>

Reaching it from outside your home? Put a reverse proxy with HTTPS in front: [Running it on the internet](docs/user-guide.md#running-it-on-the-internet).
Everything else (adding music, every feature, updating, backups, troubleshooting) is in the [user guide](docs/user-guide.md).

## Why NxtSound?

Streaming services rent you your own taste back, month after month. A folder of files on your own disk has none of that convenience:
no good search, no recommendations, no phone app. NxtSound closes that gap.

- **You own everything.** Files stay in your folder, history stays in a small database on your server. Nothing is sent anywhere
  (the few optional internet lookups are listed in the [privacy notes](docs/user-guide.md#privacy) and can be switched off).
- **It feels like a streaming service.** Search as you type, artist and album pages, a home page that learns what you like, playlists, lyrics, a queue you can drag around, radio from any song.
- **It copes with messy libraries.** Missing tags? Artist, album, track and year are read from folder and file names.
- **Made for more than one person.** Every family member gets their own login, history, playlists and recommendations.
- **One container, no setup marathon.** Python and SQLite. No external database, no message queue.
- **Open and extensible.** Other sources (internet radio, your Nextcloud, …) connect as [plugins](PLUGINS.md): separate programs, so the core stays small and you only run what you trust.

## Features

| | Feature | Status |
|---|---|---|
| 🎵 | Library scan with tags, embedded covers, folder images and name-based detection | Stable |
| 🔎 | Instant search for songs, artists, albums; artist and album pages | Stable |
| ▶️ | Web player: queue, shuffle, repeat, drag to reorder, keyboard shortcuts, lyrics, resume position | Stable |
| 📃 | Playlists, likes, listening history, "stop recommending this" | Stable |
| 🏠 | Home page: "Your Mix", "Because you listened to …", automatic lists (most played, rediscover, genres, decades) | Stable |
| 👥 | Multiple users, admin area, backup and restore | Stable |
| 📱 | Installable web app (PWA), mobile friendly; English and German | Stable |
| 🎚 | DJ mode and DJ fit (tempo and key aware radio, beat-matched transitions) | Alpha |
| 📊 | Your recap (week / month / year: minutes, top songs, streaks, …) | Alpha |
| 🧩 | Plugin system (protocol v1) | Alpha |
| 🌐 | Optional metadata from MusicBrainz and the Cover Art Archive | Alpha |
| 🔔 | Notification bell, update notices for server and plugins, disk space warnings | Alpha |
| 🤝 | Community: follow friends, see what they listen to (opt-in, off by default) | Alpha |
| 🍏 | Native iOS app with lock screen controls and offline songs | In development |

**Stable** = used every day and covered by tests. **Alpha** = works, but young: details may change and rough edges are expected. Please report what you find.

<table>
  <tr>
    <td width="33%"><img src="docs/screenshots/player.svg" alt="Player"></td>
    <td width="33%"><img src="docs/screenshots/search.svg" alt="Search"></td>
    <td width="33%"><img src="docs/screenshots/playlists.svg" alt="Playlists and automatic lists"></td>
  </tr>
  <tr>
    <td width="33%"><img src="docs/screenshots/recap.svg" alt="Your recap"></td>
    <td width="33%"><img src="docs/screenshots/community.svg" alt="Community"></td>
    <td width="33%"><img src="docs/screenshots/admin-plugins.svg" alt="Admin area: plugins"></td>
  </tr>
</table>

## Plugins

Music does not have to come from files on your disk. A plugin is a small separate program that adds a source; the server sets it up and starts it,
and its songs show up in search, radio, playlists and recommendations.

- [Online radio](https://github.com/nxtsound/nxtsound-plugin-onlineradio): live stations from radio-browser.info, with the current song
- [Nextcloud / ownCloud](https://github.com/nxtsound/nxtsound-plugin-nextcloud): the music in a folder of your cloud
- Your own: the [plugin guide](PLUGINS.md) and the runnable template in [`examples/hello-plugin`](examples/hello-plugin)

Plugins run with the rights of the server: only install plugins you trust.

## Roadmap

- **Now:** first public release (0.1.0), collecting feedback on the alpha features.
- **Next:** 🍏 the **iOS app** (built, being tested; TestFlight / App Store) · 🌐 the **project website** (nxtsound.app) with a tour and a live demo.
- **Later:** 🤖 an **Android app** (wanted, see below) · 🧩 a directory of community plugins. Ideas welcome: open an issue.

## Support the project

NxtSound is free and open source (MIT) and developed in spare time. If it brings you joy you can chip in; donations are voluntary and buy no features or priority.

| | |
|---|---|
| ☕ **Ko-fi** | `KOFI_LINK_PLACEHOLDER` |
| 💙 **PayPal** | `PAYPAL_LINK_PLACEHOLDER` |

**What donations pay for:** the Apple Developer Program (about 99 USD per year) for the iOS app · the domain, website and a public demo server · test devices (for example for the Android app) · and, if anything is left, time for the project.
No money? A star, a bug report, a translation or a mention to a friend helps just as much.

## Contributing

Contributions are very welcome. Some of the most valuable are not code in this repository:

- 🤖 **An Android app** is the biggest gap. The iOS app is a thin native shell around the web app (background playback, lock screen, offline songs); Android can follow the same idea. If you know Kotlin and want to lead it, open an issue and we plan it together. The server side is ready.
- 🧩 **Plugins** for other music sources · 🌍 **translations** (one JSON file, no code) · 🐛 **bug reports** · 📖 **docs** · 📸 **screenshots**

In short: fork, `pip install -r requirements-dev.txt`, change, `python -m pytest -q tests`, open a pull request.
The step-by-step guide and our rules are in [CONTRIBUTING.md](CONTRIBUTING.md) and the [developer guide](docs/developer-guide.md).

## Documentation

| For | Where |
|---|---|
| Using NxtSound: install, add music, every feature, updates, troubleshooting, privacy | [User guide](docs/user-guide.md) |
| All settings (environment variables) | [Configuration reference](docs/configuration.md) |
| How the code is organized, how to run, test and change it, how to release | [Developer guide](docs/developer-guide.md) |
| Writing a plugin | [Plugin guide](PLUGINS.md) |
| What changed · reporting a vulnerability | [Changelog](CHANGELOG.md) · [Security policy](SECURITY.md) |

## License

[MIT](LICENSE). Third-party services keep their own terms (MusicBrainz, Cover Art Archive, LRCLIB, radio-browser.info).
