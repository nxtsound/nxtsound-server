# Changelog

All notable changes are listed here. The project follows [semantic versioning](https://semver.org); while the version starts with `0.`,
minor versions may contain breaking changes, which are called out.

## [Unreleased]

## [0.1.0] – first public release

Alpha. The core is in daily use; the features marked *alpha* in the README may still change.

### Library and playback
- Your own music from a folder: scan with tags, embedded covers and folder images; missing tags are read from folder and file names.
- Search as you type for songs, artists and albums; artist and album pages.
- Web player with queue (drag to reorder), shuffle, repeat, synchronized lyrics, resume position for long tracks, keyboard shortcuts.
- Playlists, likes, listening history, "stop recommending".
- Home page with "Your Mix", "Because you listened to …" and automatic lists (most played, rediscover, genres, decades).
- DJ mode and DJ fit: tempo and key aware radio, beat-matched transitions (alpha).
- Your recap for week, month, year and all time (alpha).

### Server
- Multiple users with an admin area, password change, backup and restore.
- Optional metadata from MusicBrainz and the Cover Art Archive (alpha).
- Plugin system, protocol v1, with per-plugin settings, actions, search categories, a log per plugin, install from repository or ZIP, update checks (alpha).
- Notification bell for everyone; system messages for admins: low disk space, new plugin versions, new server version (alpha).
- Community: follow each other, opt-in "share what I listen to", friends on the home page; off by default (alpha).
- English and German user interface.
- Security: salted scrypt password hashes, hashed session tokens, CSRF protection, origin check, request size limits, security headers, generic error answers.

### Packaging
- Docker image (amd64, arm64) and `docker compose` setup.
- The web app can be opened from the cache without the server (for the native apps).
- `GET /api/me/offline-set` for apps that keep songs offline.
