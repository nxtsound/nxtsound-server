# Changelog

All notable changes are listed here. The project follows [semantic versioning](https://semver.org); while the version starts with `0.`,
minor versions may contain breaking changes, which are called out.

## [Unreleased]

### Added
- **Remote control:** the new *Devices* button (in the header, the player bar and the big player) lists the other devices of your account that have something loaded and steers them: play and pause, next and previous, jump to a position, volume. A green dot on the header button pulses while another device is playing and appears or disappears by itself as soon as a stream starts or ends (no reload). Playing something on the device in your hand does not stop the other one.
- **Login alerts:** when an account is logged in from a device (browser and system) or a network (address group) the user has not used before, they get a message in their bell that links to *Account → Devices*. The very first login of a user raises nothing. Admin area → System → Login alerts turns it off (`LOGIN_ALERTS`).
- **Devices:** Account → Devices lists every browser and app you are logged in on (name, browser and system, when you logged in, when it was last used, IP address). Give a device a name, log a single device out, or log out everywhere else.
- **Limit for playing at the same time:** Admin area → System → Playing at the same time sets how many streams of one account may play at once (`MAX_STREAMS_PER_USER`, default 0 = no limit). Every player counts, a browser tab as well as an app, even several in the same login. A further player is told so when it tries to start and does not play; the ones that already play are not disturbed. Pausing or closing the tab frees the place. The admin also sees who plays right now.
- **Login protection you can set:** Admin area → System → Login protection sets how many failed logins an address may make and how long it is blocked afterwards (defaults 5 and 5 minutes, `LOGIN_MAX_ATTEMPTS` and `LOGIN_BLOCK_MINUTES`). Blocks survive a restart and can be lifted there. `0` attempts turns the protection off.
- **A home page you can arrange:** *Customize home* (at the bottom of the home page) lets every user choose which sections appear, their order and their options (rows, number of songs, period). Besides the existing sections there are new ones: *Rediscover*, *Your top songs* (week, month, year, all time), *Genres*, *Decades*, *Artist mixes*, *Discover*, *Your playlists*, *Shuffle your liked songs*, *Recap in numbers*, *A year ago* and *Your mix for this time of day*. Sections with nothing to show stay hidden; *Back to the default* restores the original page.
- **Crossfade (part of DJ mode):** the DJ menu has a new style *Crossfade*: the end of a song fades out while the next one fades in from its start, with a length of 3, 6, 9 or 12 seconds. It needs no tempo analysis. With *Crossfade songs that cannot be matched on the beat* (on by default) DJ mode also blends songs whose tempo could not be detected or matched, instead of cutting from one to the next. Like the rest of DJ mode it is not available on iPhone and iPad.
- **More keyboard shortcuts and a list of them:** a keyboard button in the top bar (computer only) or the key `?` opens a window with all shortcuts. New: `S` shuffle, `R` repeat, `A` add to a playlist, `F` big player, `Q` queue, `0` to `9` jump to 0 to 90 % of the song, `G` then `H`, `P`, `L` or `C` go to Home, Playlists, Liked songs or Community.
- **Backups to other places:** Admin area → System → Backups to other places sends a copy of the database to a folder on the server (also an NFS share), to S3 (Amazon, MinIO, Backblaze ...), to Nextcloud or ownCloud (WebDAV), to FTP/FTPS or to SFTP: by hand or every 6 hours, 12 hours, daily or weekly, keeping the last N copies. A failed backup raises a message for the admins. The backups of a destination can be listed, deleted and restored. The Docker setup mounts `BACKUP_PATH` (default `./backups`) at `/backups`. New dependency: `paramiko` (SFTP).

### Changed
- **Calmer home page:** "Recently played" is smaller (2 x 2 per page, three pages at most), "Recently added" comes right after it instead of at the bottom, "Your Mix" spans the full width and keeps its red divider, and each "Because you listened to ..." shelf starts with the cover of its song.
- The **Playlists** page now shows only *Liked songs* and your own playlists. The computed "For you" lists (*Most played*, *Rediscover*, genre and decade mixes, artist mixes) are no longer listed there.

### Fixed
- `Esc` closed a window and the big player behind it at the same time; now it closes one thing per press.
- The home page now refreshes by itself after a song counts as listened (it silently did nothing before: a helper function was not reachable from there).


## [0.2.0] – 2026-10-21

### Added
- **Update channels:** Admin area → System → Updates lets you choose *stable* (tested releases, default) or *beta* (also pre-releases such as `0.2.0-beta.1`). `UPDATE_CHANNEL` sets the default.
- The update window shows both ways to update (ready-made image and built from the source) and says when it is a pre-release.
- Release workflow: a version tag builds the image; a stable tag prepares a draft release with the notes from this changelog, a beta tag (`v0.2.0-beta.1`) publishes a pre-release and the image tag `beta`. `latest` only ever points to stable.

### Changed
- Versions are compared like semantic versioning (`0.2.0-beta.2` is newer than `-beta.1` and older than `0.2.0`).
- The release notes in the update window scroll after about 15 lines.
- Docker images report the version of the tag they were built for (`0.2.0-beta.1`), so a beta never announces itself as an update.

## [0.1.1] – 2026-10-21

### Fixed
- A plugin's search tabs (for example "Live radio") did not appear after installing, enabling or updating the plugin until the page was reloaded: sources and tabs are now looked up again when the search opens and when a plugin has finished starting.
- While a plugin was being installed, the admin page redrew itself completely every three seconds (losing what you had typed). Now only the plugin's own card is refreshed.

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
