# User guide

This guide takes you from nothing to a running music server, then walks through everything the web app can do.
You do not need to be a developer. If you can copy a few commands into a terminal, you can run NxtSound.

**Contents**
1. [What you need](#what-you-need)
2. [Installing](#installing)
3. [Your first login](#your-first-login)
4. [Preparing your music](#preparing-your-music)
5. [Using the web app](#using-the-web-app)
6. [Your account](#your-account)
7. [Admin area](#admin-area)
8. [Plugins](#plugins)
9. [Community](#community)
10. [Notifications and updates](#notifications-and-updates)
11. [Phone apps](#phone-apps)
12. [Backup and restore](#backup-and-restore)
13. [Running it on the internet](#running-it-on-the-internet)
14. [Privacy](#privacy)
15. [Troubleshooting](#troubleshooting)

---

## What you need

- A computer that stays on: a home server, a NAS that can run Docker, a mini PC, or a small rented server.
- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin (the command `docker compose` must work).
- A folder with your music files. MP3, FLAC, M4A/AAC, OGG, Opus, WAV and AIFF are understood.

Without Docker it works too (Python 3.12 and `ffmpeg`), see the [developer guide](developer-guide.md#running-from-source).

## Installing

```sh
git clone https://github.com/nxtsound/nxtsound-server.git
cd nxtsound-server
cp env.example .env
```

Open `.env` in a text editor and set at least these three lines:

```ini
MUSIC_PATH=/path/to/your/music      # the folder on the host that holds your music
ADMIN_USER=admin                    # the first account (an administrator)
ADMIN_PASSWORD=choose-a-long-one    # at least 8 characters
```

Start it:

```sh
docker compose up -d --build
```

The web app is now on port 8000: open `http://<address of your server>:8000` (on the same computer: <http://localhost:8000>).

What the setup does for you:

- The music folder is attached **read-only**: NxtSound never changes or deletes your files.
- A Docker volume called `app-data` keeps the database, covers and plugin data. Your music and this volume are all you need to keep.
- A `plugins` folder next to the compose file holds plugins (see [Plugins](#plugins)).
- The container restarts by itself after a reboot.

The admin account is only created if the database is empty, so you can remove `ADMIN_USER` and `ADMIN_PASSWORD` from `.env`
after the first start. If you start without them, the login page asks you to create the first account instead.

**Using the published image instead of building:** change `build:` to `image: ghcr.io/nxtsound/nxtsound-server:latest` in
`docker-compose.yml` (or pin a version such as `:0.1.0`) and use `docker compose pull && docker compose up -d`.

### Updating

```sh
cd nxtsound-server
git pull
docker compose up -d --build
```

Your data stays in the volume. Open the admin area → System to see the version. The server tells admins when a new version
exists (the bell), but it never updates itself.

## Your first login

Open the address in your browser and log in. The server scans the music folder by itself; a small library is ready in seconds,
a large one takes a while (the admin area → System shows the progress). You can use the app while it scans.

A short introduction opens the first time you log in. After an update a "What's new" card shows the changes once.

**Make it feel like an app:** on a phone, open the address in Safari (iPhone) or Chrome (Android) and choose
"Add to Home Screen". It then opens full screen without the browser bars. (Background playback on the iPhone is much better with the
native app, see [Phone apps](#phone-apps).)

## Preparing your music

NxtSound works with tidy and with messy libraries. Tags (title, artist, album, year, track number, genre) are used when they exist.
When they do not, the server reads them from folder and file names, and the two styles can be mixed.

| Layout | Result |
|---|---|
| `Artist/Album/03 - Title.mp3` | artist, album, track 3, title |
| `Artist/2020 - Album/CD2/05 Title.flac` | additionally year 2020 and disc 2 |
| `Artist/Album (2020)/Title.mp3` | year from the album folder |
| `Artist - Title.mp3` | artist and title (album = folder, if there is one) |
| `Artist - Album - 04 - Title.mp3` | artist, album, track 4, title |
| `Title.mp3` | title only, artist "Unknown artist" |

Good to know:

- A leading number only counts as a track number with a separator (`01 - `, `1-05 `, `03. `) or a leading zero. "99 Luftballons" stays a title.
- **Covers**: embedded pictures are used first, then an image in the album folder named `cover`, `folder`, `front`, `album` or `albumart`.
- If you rename files or folders, the server sees new files (new IDs). Playlists and likes of the old names are lost, so decide on a layout early.
- New and changed files are found at startup and with every scan. Set `SCAN_MINUTES=15` to scan every 15 minutes, or use
  Admin area → System → **Scan now** (and "Re-read everything" after you changed tags or covers on existing files).
- **Missing details** (year, cover, a short artist description) are filled in from MusicBrainz and the Cover Art Archive in the
  background, if you leave that on. Tags and covers you already have are never overwritten. The cleaner your names, the more hits.

## Using the web app

### Home

The home page learns from what you play:

- **Recently played**: pick up where you left off.
- **Your Mix**: tracks similar to what you listened to lately.
- **Because you listened to …**: similar tracks to individual songs.
- **Recently added** and **Discover**: new files and random picks, which is also what a brand-new account sees.
- **Your artists**, and, with the [community](#community) on, **What your friends listen to**.

Pull down on a phone to refresh.

### Search

Tap the magnifier (or press `/` on a computer). Results appear as you type: your own library first, then plugin sources. Tabs switch
between songs, artists and albums, and between extra categories that plugins add (like "Live radio"). The tab with the most hits is
selected for you. Your recent searches and picks are remembered for quick access.

### Artists and albums

Tap an artist or album anywhere to open its page: popular songs, albums, singles and EPs, a short description if one was found, and buttons to
play, shuffle or start a radio.

### The player

The bar at the bottom shows what plays. Tap it to open the full-screen player:

- play, pause, previous, next, seek, volume (on a computer)
- **shuffle** and **repeat** (off / all / one song)
- **like** a song (♥), **add to a playlist**, jump to the artist or album
- **lyrics**: synchronized when available, scrolling along with the song
- **queue**: see what is next, drag songs to reorder, remove songs, "clear the rest"
- **start radio**: plays songs that fit the current one (similar artists, tempo and key when analyzed)
- long songs (for example mixes and podcasts) **remember your position**

The ⋮ menu next to any song offers: play next, add to queue, add to playlist, like, start radio, go to artist or album, and
**"Stop recommending"** (hides this song or artist from your recommendations without deleting anything).

**Keyboard shortcuts** (computer): `Space` or `k` play/pause · `n` / `p` next/previous · `←` `→` seek 10 s · `↑` `↓` volume ·
`m` mute · `l` like · `/` search · `Esc` close.

### DJ mode (alpha)

With DJ mode on, the radio orders songs into a chain with matching tempo and compatible keys, and the player blends one song
into the next on the beat. The server analyzes tracks in the background after each scan (turn off with `ANALYZE=0`). DJ mode is off
on iPhones and iPads, where the system does not allow precise timing for background audio.

### Playlists and likes

- **Playlists** → *New playlist*, or use "Add to playlist" in any ⋮ menu. Rename or delete a playlist on its own page, and remove songs with their ⋮ menu.
- **Liked songs** collects everything you liked with ♥.
- **For you** (under Playlists) holds lists that are computed fresh on every visit from your history: *Most played*, *Rediscover*
  (not heard for 30 days), *Mix: Artist* for your top artists, *genre mixes*, *decades* and *Recently added*. "Save as playlist" keeps one for good.

### Your recap (alpha)

Account → **Recap** shows your listening for the last week, month, year or all time: minutes, top songs and artists, your most
active hours and days, and your listening streak. It is computed from your own history on your own server and is never shared.

## Your account

Open the round button at the top right: change your **name**, your **password**, the **language** (automatic, English or Deutsch) and see a summary
of the last 30 days. Admins also find the admin area here.

## Admin area

Only admins see it. It has four tabs.

**Overview**: how many users and tracks, listening time, and the health of the server, including how fast songs start on computers and on mobile.

**Users**: create accounts (with an initial password the person changes on first login), make someone an admin, reset a password,
delete an account (including its playlists and history).

**Plugins**: see [Plugins](#plugins).

**System**:
- **Library**: scan now / re-read everything, and where the music folder is.
- **Information from the internet**: turn the MusicBrainz lookups on or off, run them now.
- **Updates**: the server version, "Check for updates", and what is available.
- **Community**: turn the feature on or off for everyone.
- **Logging**: choose how much the server logs, per category, and read the latest messages.
- **Backup**: download or restore a backup of the database.

## Plugins

Music can come from other places than your disk. A plugin adds a source: internet radio, your Nextcloud, and more. Plugins are
separate programs; the server sets each one up in its own environment and starts it.

**Install** (Admin area → Plugins), pick one way:

| Way | How |
|---|---|
| From a repository | Enter the plugin's GitHub address, for example `https://github.com/nxtsound/nxtsound-plugin-onlineradio`, and press Install. The first time can take a few minutes. |
| From a ZIP | Press "Upload a ZIP" and choose the file (GitHub's "Download ZIP" works). Upload a newer ZIP later to update it. |
| By hand | Put the plugin folder into the `plugins` folder next to the compose file and restart the server. |

**Using a plugin**: each plugin is a card. Open it to see its description and version, turn it on or off, restart it, open its
**settings** (the gear), read its **log** (the "Log" button shows what the plugin and the server said about it), update or remove it.
A green **Update available** button appears when a newer version exists; it opens a window with the changes.

**Safety**: a plugin runs as code on your server with the rights of the server. Only install plugins you trust. If other people can
reach your admin area, you can forbid installing with `PLUGIN_REPO_INSTALL=0` and `PLUGIN_UPLOAD=0`.

Official plugins:

- **[Online radio](https://github.com/nxtsound/nxtsound-plugin-onlineradio)** adds a "Live radio" tab to the search with stations from radio-browser.info and shows the current song.
- **[Nextcloud / ownCloud](https://github.com/nxtsound/nxtsound-plugin-nextcloud)** plays a music folder from your cloud.

## Community

Off by default. An admin turns it on in Admin area → System → Community. Afterwards there is a **Community** entry in the menu:

- see everyone on the server and **follow** them; people get a message in their bell when you follow them
- tick **"Share what I listen to with the people who follow me"** to let your followers see the songs you played in the last 7 days.
  This is **off for everybody until they switch it on**, and switching it off hides everything again, past plays included
- followed friends who share appear on your home page as **What your friends listen to**

Nobody sees anything about anybody who has not chosen to share. Admins see no more than anybody else.

## Notifications and updates

The **bell** at the top right collects messages for you (for example "Maria follows you"). Admins also get **system messages**:

- disk space is getting low (below `DISK_WARN_GB` or `DISK_WARN_PERCENT`)
- a newer version of a plugin you installed from a repository exists
- a newer NxtSound server version exists (it follows the project's tags and releases on GitHub)

The server checks every 6 hours (`UPDATE_CHECK_HOURS`; `0` turns every check off, so nothing at all is sent to GitHub).
Checking only reads public information. It never installs anything by itself.

## Phone apps

- **Web app** (PWA): works on any phone, see [Your first login](#your-first-login).
- **iOS app**: a native app with lock screen controls, reliable background playback and offline songs (the last 20 you played and your
  20 most played, switchable in the app settings, only on Wi-Fi by default). It is built and being tested, and not released yet.
- **Android app**: not available yet. [Contributors welcome](../CONTRIBUTING.md#android-app).

## Backup and restore

Your music files are your music; the **database** holds accounts, playlists, likes and history.

- **Backup**: Admin area → System → *Download database backup*. Keep it somewhere safe: it contains password hashes and the access tokens of your plugins.
- **Restore**: *Restore backup …* and choose the file. The server checks it, saves a copy of the current state as
  `before-restore-<time>.db` in the data folder, signs everyone out, replaces the data and scans the library again.
- Music files and cover caches are not part of the backup.

Without the web app: stop the container and copy the Docker volume (`app-data`), or back up the folder you mounted as `/data`.

## Running it on the internet

At home, `http://<address>:8000` is fine. To reach it from outside:

1. Put a **reverse proxy** with **HTTPS** in front (Caddy, nginx, Traefik, …). Never expose port 8000 directly to the internet.
2. Tell the server it is behind a proxy: `TRUST_PROXY=1` in `.env`, **only** if the proxy sets `X-Forwarded-For`, `-Proto` and `-Host`.
   Keep it `0` otherwise. Bind the port to the proxy only: `"127.0.0.1:8000:8000"` in `docker-compose.yml`.
3. If you use a different public name than the one the server sees, list the allowed origins in `ALLOWED_ORIGINS` (comma separated).
4. Use a long admin password. Consider `PLUGIN_REPO_INSTALL=0` and `PLUGIN_UPLOAD=0` if other people can reach the admin area.

**Caddy** (the whole config, HTTPS is automatic):

```
music.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx**:

```nginx
server {
    listen 443 ssl http2;
    server_name music.example.com;
    # ssl_certificate ... (e.g. from Let's Encrypt)

    client_max_body_size 100m;           # plugin ZIPs (up to 50 MB) and backups

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;             # smoother streaming
        proxy_read_timeout 300s;
    }
}
```

**Uploads fail with a plain error page (403, 413)?** Then the message comes from the proxy or a web application firewall, not
from NxtSound. Allow request bodies up to 50 MB for `/api/admin/plugins/upload` (and up to the size of your backup for
`/api/admin/restore`), and let `multipart/form-data` pass the firewall rules (ModSecurity, BunkerWeb and the like) for these two paths.
Reaching the server's port directly always works for a test.

## Privacy

- Your music, history, playlists and likes live on **your** server only.
- The server only talks to the internet in the cases below. Every one of them can be switched off.

| What | Why | Switch off |
|---|---|---|
| `api.github.com` | looks for a newer NxtSound release and for newer versions of plugins you installed from a repository (it never updates anything by itself) | `UPDATE_CHECK_HOURS=0` |
| MusicBrainz, Cover Art Archive | fills in missing year, cover and artist description (only artist and album names are sent) | `ENRICH=0` |
| lrclib.net | song lyrics (only the song title, artist, album and length are sent) | `LYRICS=0` |
| Plugins you install | whatever the plugin does (for example the radio plugin asks radio-browser.info) | do not install them |

- Passwords are stored as salted hashes; sessions expire; the database only holds a hash of your session cookie.
- Logs never contain passwords, and failed logins are not logged with names.
- Community sharing is opt-in per person.

## Troubleshooting

**The page does not load.** Is the container running? `docker compose ps`. What does it say? `docker compose logs server`
(also: Admin area → System → Logging → "Show recent messages").

**My music is not showing up.** Check `MUSIC_PATH` in `.env` (the folder must exist on the host), then Admin area → System → Scan now.
A track is only listed if it is an audio file of a supported format.

**Tracks have "Unknown artist".** They have no tags and no usable folder structure. Add tags, or use one of the layouts above, then "Re-read everything".

**I cannot log in / I forgot the admin password.** Another admin can reset it (Users). If you are the only admin, restore a backup, or stop
the server, delete the database file in the data volume (this removes all users and history) and start again with `ADMIN_USER` and `ADMIN_PASSWORD` set.

**Login works but I am logged out all the time.** Behind a proxy with `COOKIE_SECURE=auto` the server needs `TRUST_PROXY=1` to know the connection is HTTPS. See [Running it on the internet](#running-it-on-the-internet).

**Songs stutter or start slowly on the phone.** Check the Overview tab for how long songs take to start. Behind a proxy, turn off response buffering (`proxy_buffering off`).

**A plugin shows an error.** Open its card: the message is shown there, and "Log" shows what the plugin printed. "Restart" often helps; "Update" fixes known problems.

**The lyrics are wrong or missing.** Lyrics come from [LRCLIB](https://lrclib.net), a community database. Not every song is in it.

**Still stuck?** [Open an issue](https://github.com/nxtsound/nxtsound-server/issues) with the server version (bottom of the home page), what you did, and the log lines around the problem.
