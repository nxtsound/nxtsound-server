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

Without Docker it works too (Python 3.12), see the [developer guide](developer-guide.md#running-from-source) or the README.

## Installing

There are two ways. Most people want the first.

### Option A: the ready-made image (recommended)

No download of the source code, no build. Create a folder, put this into a file called `docker-compose.yml`, and change the two marked lines:

```yaml
services:
  nxtsound:
    image: ghcr.io/nxtsound/nxtsound-server:latest      # or pin a version, e.g. :0.1.0
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
```

Start it with `docker compose up -d`. More settings can be added under `environment:` (see the [configuration reference](configuration.md)).

### Option B: build from the source

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

### After starting

The web app is now on port 8000: open `http://<address of your server>:8000` (on the same computer: <http://localhost:8000>).

What the setup does for you:

- The music folder is attached **read-only**: NxtSound never changes or deletes your files.
- A Docker volume keeps the database, covers and plugin data. Your music and this volume are all you need to keep.
- A `plugins` folder next to the compose file holds plugins (see [Plugins](#plugins)).
- The container restarts by itself after a reboot.

The admin account is only created if the database is empty, so you can remove `ADMIN_USER` and `ADMIN_PASSWORD` afterwards.
If you start without them, the login page asks you to create the first account instead.

### Updating

- **Option A (image):** `docker compose pull && docker compose up -d`.
- **Option B (source):** `cd nxtsound-server && git fetch --tags && git checkout v0.1.1 && docker compose up -d --build` (use the version the admin area names; the project is developed on `main`, so a plain `git pull` can give you a state that is newer than the last release).

Your data stays in the volume. Open the admin area → System to see the version. The server tells admins when a new version
exists (the bell, and a window with the release notes and both ways to update), but it never updates itself.

**Stable or beta:** In Admin area → System → Updates you choose where updates come from. *Stable* (the default) announces tested releases only.
*Beta* also announces test versions (`0.2.0-beta.1`, shown as "Beta available") before they are released as stable, so you can try new things early and help finding problems.
A beta can have bugs; keep a [backup](#backup-and-restore). With the image, a beta is the tag `:beta` (`image: ghcr.io/nxtsound/nxtsound-server:beta`); `:latest` is always the newest stable version.
Switching the channel only changes what you are told about; the version that runs changes when you update it as described above.

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
- **Covers**: embedded pictures are used first, then a picture named like the song (`Title.jpg` next to `Title.m4a`), then an image in the album folder named `cover`, `folder`, `front`, `album` or `albumart`.
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

**Make it yours:** at the bottom of the home page, *Customize home* lets you choose the sections, move them up and down, remove them and set their options
(for example the period of *Your top songs*). Besides the usual rows you can add *Rediscover*, genres, decades and artist mixes, your playlists, a button that shuffles your
liked songs, your recap in numbers, what you listened to a year ago and a mix for the current time of day. Sections without anything to show stay hidden.
Changes are saved at once; *Back to the default* restores the original page. Every user has their own layout.

### Search

Tap the magnifier (or press `/` on a computer). Results appear as you type: your own library first, then plugin sources. Tabs switch
between songs, artists and albums, and between extra categories that plugins add (like "Live radio"). The tab with the most hits is
selected for you. Your recent searches and picks are remembered for quick access.

### Artists and albums

Tap an artist or album anywhere to open its page: popular songs, albums, singles and EPs, a short description if one was found, and buttons to
play, shuffle or start a radio.

### The player

**Steering another device:** if music plays on one of your devices (say your computer) you can steer it from another (your phone): tap the **Devices** button (the little screen and phone
icon at the top, in the player bar and in the big player). It lists your other devices that have a song loaded, with play/pause, previous, next, a position slider and the volume.
A green dot on the button tells you, live, that another device is playing: it appears and disappears by itself. Tapping a song on the device in your hand plays it there and leaves the other one alone.
A phone that was put to sleep may not receive commands before you wake it, and some browsers refuse to start playback without a tap on that device (pause and skip always work).

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

**Keyboard shortcuts** (computer): the keyboard button in the top bar (or the key `?`) shows them all. `Space` or `k` play/pause · `n` / `p` next/previous ·
`←` `→` seek 10 s · `0`–`9` jump to 0–90 % of the song · `↑` `↓` volume · `m` mute · `s` shuffle · `r` repeat · `l` like · `a` add to a playlist · `f` big player ·
`q` queue · `/` search · `g` then `h`, `p`, `l` or `c` go to Home, Playlists, Liked songs or Community · `Esc` close.

### DJ mode (alpha)

With DJ mode on, the radio orders songs into a chain with matching tempo and compatible keys, and the player blends one song
into the next on the beat. The server analyzes tracks in the background after each scan (turn off with `ANALYZE=0`). DJ mode is off
on iPhones and iPads, where the system does not allow precise timing for background audio.

**Made by the server:** for songs of your library the transition itself is made by the server (a short audio file, the *bridge*, with the end of one song and
the start of the next lined up exactly, the tempo matched and the effect applied). The player fades over to it a few seconds before the transition and back to
the next song a few seconds after it. While it plays the screen says *Old song > New song* by *NxtSound DJ*; play, pause, jumping and skipping work as always
(skipping plays the next song from its start). Songs from a plugin, and songs without a clear beat, are blended by the player as before. The menu has an option
*Let the server make the transitions* to turn this off.

**Crossfade:** in the DJ menu (tap the DJ button in the big player) choose the style *Crossfade* to simply fade one song out while the next fades in, without matching the tempo.
The length (3, 6, 9 or 12 seconds) is in the same menu. With the option *Crossfade songs that cannot be matched on the beat* switched on (the default), the other styles fall back to a
crossfade when two songs do not fit, instead of cutting from one to the next. Very short songs (under 30 seconds) and live streams are never blended.

### Playlists and likes

- **Playlists** → *New playlist*, or use "Add to playlist" in any ⋮ menu. Rename or delete a playlist on its own page, and remove songs with their ⋮ menu.
- **Liked songs** collects everything you liked with ♥.
- **Computed lists** are made fresh on every visit from your history: *Most played*, *Rediscover* (not heard for 30 days), *Mix: Artist* for your top artists, *genre mixes*, *decades*
  and *Recently added*. They are not on the Playlists page (which shows only your own playlists and your liked songs); open them from the home page sections. "Save as playlist" on a list keeps it for good.

### Your recap (alpha)

Account → **Recap** shows your listening for the last week, month, year or all time: minutes, top songs and artists, your most
active hours and days, and your listening streak. It is computed from your own history on your own server and is never shared.

## Your account

Open the round button at the top right: change your **name**, your **password**, the **language** (automatic, English or Deutsch) and see a summary
of the last 30 days. Admins also find the admin area here.

**Devices:** *Account → Devices* lists every browser and app you are logged in on, with the browser and system, when you logged in, when it was last used and
from which IP address. You can give a device your own name, log a single device out (it has to log in again) or log out all other devices at once. Do this
when you lose a phone or used a computer that is not yours. The server keeps the address of a login only for this list, until the login ends.
Changing your password logs out every device.

**Login alerts:** if your account is logged in from a device or network you have not used before, a message appears in the bell (top right) saying which device and from which
address. If it was not you, open *Account → Devices*, log that device out and change your password. A "network" is the address group of your internet connection, not a place on the map;
the server never asks anyone where an address is. The admin can switch the alerts off.

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
- **Playing at the same time**: how many streams of one account may play at once (default: no limit) and who plays right now. Every browser tab or app that plays counts. One over the limit gets a message and does not start; the others keep playing.
- **Login protection**: how many wrong passwords are allowed before an address is blocked and for how long, the blocks running now (with a button to lift each one or all). The defaults are 5 attempts and 5 minutes. A block survives a restart. A block ends by itself; another admin can lift it earlier.
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

### Backups to other places (automatic)

Downloading by hand is easy to forget. Admin area → System → **Backups to other places** sends a copy of the database to a destination you choose, by hand (*Back up now*) or on a schedule.
The music files are not part of it. You can add up to ten destinations, each with its own schedule.

| Destination | What you enter | Notes |
|---|---|---|
| **Folder on the server** | the path, for example `/backups` | The Docker setup mounts `BACKUP_PATH` (default `./backups`) at `/backups`. For **NFS**, mount the share on the host and point `BACKUP_PATH` to it, or give the container an NFS volume (see below). |
| **S3** (Amazon, MinIO, Backblaze B2, Wasabi ...) | endpoint, region, bucket, an optional folder in the bucket, access key and secret key | Addresses of the form `endpoint/bucket/...` (path style). Give the key only the rights it needs for this bucket. |
| **Nextcloud / ownCloud / WebDAV** | the address of your cloud (`https://cloud.example.com`), user name, an **app password**, a folder | The folder is created if it is missing. A full WebDAV address also works. |
| **FTP / FTPS** | server, port, user, password, folder, encryption | Use FTPS if the server offers it. "Accept any certificate" is for servers with a self-signed certificate. |
| **SFTP** (SSH) | server, port, user, a password **or** a private key, folder | The server's key is remembered at the first connection and checked from then on; a changed key stops the backup until you clear the fingerprint. |

**Schedule:** every 6 or 12 hours, daily or weekly (Sundays) at a time you set (server time, in Docker usually UTC). If the server was off at that time, the backup is made when it is back.
A failed backup is tried again every half hour, and the admins get a message in the bell until one works. **Keep the last** says how many backups stay on the destination; older ones are deleted
(only files that this server made, named `nxtsound-<date>-<time>.db`: other files in the folder are never touched). *Test the connection* tries the settings without saving them.

**Restore from a destination:** the *Backups* button of a destination lists what is there, newest first. *Restore* does the same as restoring an uploaded file: it checks the backup,
saves a copy of the current state in the data folder, replaces the data and signs everybody out.

**Keep in mind:** a backup contains password hashes, the settings of your plugins and the passwords and keys of your destinations. The passwords of the destinations stay on the server (the admin page never shows them again;
leave a password field empty to keep it), but a backup file that lies on a cloud contains them. Use an account or key that may only write to the backup place.

**NFS volume for Docker** (instead of mounting the share on the host):

```yaml
services:
  server:
    volumes:
      - nas-backups:/backups
volumes:
  nas-backups:
    driver: local
    driver_opts:
      type: nfs
      o: addr=192.168.1.20,rw,nfsvers=4
      device: ":/export/nxtsound-backups"
```

The container writes as user 1000: the share must allow that (on a NAS, set the squash option to map all users or give user 1000 write access).

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

**Rate limits in a firewall or proxy.** NxtSound itself has no limit for requests per second (only the [login protection](#admin-area)). If your firewall or proxy limits
requests per IP address, these are the numbers one browser causes (measured against a demo instance; yours vary with the length of your shelves and the speed of your connection):

| What happens | Requests | Note |
|---|---|---|
| Opening the home page | about 40 to 60 within 1 to 2 seconds | the biggest burst: cover pictures (about 25), the app's files and about 10 calls. Other pages with many covers are similar. |
| A device is playing | about 0.25 per second (14 per minute) | a sign of life every 15 s, the playing state every 10 s, the bell once a minute, one entry per song you listened to |
| The devices window is open | another 0.3 per second | asks for the list every 3 s |
| Typing in the search | up to about 8 per second | one suggestion request per letter |
| Starting a song | 1 to 3 | the audio file (more when you skip around in it) and the next song loaded ahead |
| Event stream (remote control) | 1 long connection per visible tab, no requests per second | a ping every 20 s |

What follows from that:

- **Several devices share one address** at home (phones, TV, computers), so their bursts add up. A limit of 100 requests per 10 seconds per IP is too tight; start at **300 per 10 seconds**
  (or about 600 per minute) for `/api/`, and raise it if people report errors while opening pages.
- Count `/api/stream/` (audio) and `/api/cover/` (pictures) separately or not at all: they are the bulk of the traffic.
- `/api/login` can be limited hard (say 10 per minute per IP). The login protection does something similar on its own, but only sees the right address with `TRUST_PROXY=1`.
- `/api/player/events` is a long-lived connection (server-sent events): switch response buffering off for it (the nginx example above does) and allow at least 60 seconds without data.
  Idle timeouts of 100 seconds or more (Cloudflare and the like) are fine, the server pings every 20 seconds.

## Privacy

- Your music, history, playlists and likes live on **your** server only.
- The server only talks to the internet in the cases below. Every one of them can be switched off.

| What | Why | Switch off |
|---|---|---|
| `api.github.com` | looks for a newer NxtSound release and for newer versions of plugins you installed from a repository (it never updates anything by itself) | `UPDATE_CHECK_HOURS=0` |
| MusicBrainz, Cover Art Archive | fills in missing year, cover and artist description (only artist and album names are sent) | `ENRICH=0` |
| lrclib.net | song lyrics (only the song title, artist, album and length are sent) | `LYRICS=0` |
| Plugins you install | whatever the plugin does (for example the radio plugin asks radio-browser.info) | do not install them |

- Passwords are stored as salted hashes; sessions expire; the database only holds a hash of your session cookie. For each login it also keeps the browser name and the IP address (at login and at last use) so you can see your devices; they are deleted when the login ends and are not part of backups. A short history of your recent logins (time, address, browser and system) stays for the login alerts.
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
