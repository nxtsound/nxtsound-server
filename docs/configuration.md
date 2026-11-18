# Configuration reference

All settings are environment variables. With Docker you put them into the `.env` file next to `docker-compose.yml` (start from
`env.example`); the compose file passes the ones it knows on to the container. Without Docker, set them in the environment of the process.
After changing `.env`, run `docker compose up -d` to apply it.

Settings marked **admin area** can also be changed at runtime there, and the admin setting wins.

## Basics

| Variable | Default | Meaning |
|---|---|---|
| `APP_NAME` | `NxtSound` | Display name in the web app and in the app's linking screen. |
| `DEFAULT_LANG` | `en` | Language (`en` or `de`) for people who have not chosen one and whose browser states no supported language. Every user can pick their own in the account. |
| `ADMIN_USER`, `ADMIN_PASSWORD` | empty | Create the first admin on the first start (only if the database is empty). You can remove them afterwards. The password needs at least 8 characters. Without them the login page asks for the first account. |

## Music

| Variable | Default | Meaning |
|---|---|---|
| `MUSIC_PATH` (Docker) | `./music` | The music folder **on the host**; it is mounted at `/music` in the container. |
| `MUSIC_ACCESS` (Docker) | `ro` | `ro` mounts the music folder read-only (NxtSound never writes there). `rw` lets plugins that keep songs save them in `<music>/<plugin>/`. |
| `MUSIC_DIR` | `/music` in Docker | The music folder as the server sees it (set this when running without Docker). |
| `IMPORT_DIR` | `<data>/imported` | A writable second library folder where plugins keep songs when the music folder is read-only. It is part of the library. |
| `SCAN_MINUTES` | `0` (Docker) | `0` = scan at startup and with the admin button only; otherwise rescan every N minutes. |
| `SCAN_ON_START` | `1` | `0` skips the scan at startup. |
| `ANALYZE` | `1` | Analyze new tracks in the background (tempo, key, intro and outro) for DJ mode and the DJ fit. `0` = off. |
| `DEVTOOLS` | `0` | `1` turns on the [developer tools](developer-guide.md#developer-tools) for admins: why a song is in the radio (the parts of its score), the plan of each DJ transition, the analysis curves, the home page seeds, calls to plugins, a fixed dice and a snapshot for bug reports. Meant for people working on the code. |
| `DJ_BRIDGES` | `1` | `0` turns off the DJ transitions made by the server (the player then blends the songs itself). They need numpy and ffmpeg, like the analysis. |
| `DJ_STEMS` | `0` | `1` leaves the singing alone in DJ transitions: the vocals of both songs are separated with the Demucs model and led on their own (the old singer finishes the phrase, the new one comes in at a phrase). Needs PyTorch and `demucs`, not built into the image: the first start with `DJ_STEMS=1` installs them into the data volume instead (see `BEAT_ML` below; needs internet access once, later starts find them there, turning both back off removes them again). About 2 GB of memory while a transition is made, 10 to 60 s per transition on a small server (the player asks early and the result is kept). |
| `DJ_STEMS_THREADS` | `4` | Processor threads for the separation (at most the number of cores). |
| `DJ_STEMS_DIR` | `<data folder>/models` | Where the model is kept once downloaded; removed when `DJ_STEMS` goes back to `0`. |
| `DJ_STEMS_CACHE_MB` | `0` | A server with `DJ_STEMS=1` separates each song once, in the background, whenever nothing is listening (a live transition always comes first; see Admin area → System for progress). `0` (default) keeps every song's result, however big the library gets; a number caps the cache in MB and drops the least recently used songs first. A transition for a song not cached yet runs the model on the spot instead, exactly like before this cache existed. |
| `DJ_STEMS_CACHE_DIR` | `<data folder>/dj-stem-cache` | Where the per-song cache is kept. |
| `DJ_BRIDGE_MB` | `300` | How much room the finished transitions may take (about 0.4 MB each); the least recently used go first. Kept in `<data folder>/dj-bridges`, or in `DJ_BRIDGE_DIR`. |
| `BEAT_ML` | `0` | `1` uses the optional `beat_this` model (needs PyTorch) for beat detection. Experimental. Shares the PyTorch install described under `DJ_STEMS`: the first start with either one on fetches it once, for both. |
| `ENRICH` | `1` | Fetch missing year, cover and artist description from MusicBrainz and the Cover Art Archive. `0` = off. **Admin area.** |
| `LYRICS` | `1` | Look up lyrics at lrclib.net. `0` = off. |

## Accounts and sessions

| Variable | Default | Meaning |
|---|---|---|
| `SESSION_DAYS` | `30` | A login ends after this many days without use. |
| `SESSION_MAX_DAYS` | `180` | A login ends after this many days at the latest, however often it is used. `0` = no limit. |
| `LOGIN_ALERTS` | `1` | `0` = no message when an account is logged in from a new device or network. **Admin area.** |
| `MAX_STREAMS_PER_USER` | `0` | How many streams (players: tabs, apps) of one account may play at the same time. `0` = no limit. **Admin area.** |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins from one address for one name before that address is blocked (four times as many across all names). `0` = never block. **Admin area.** |
| `LOGIN_BLOCK_MINUTES` | `5` | How long such a block lasts (1 to 1440). **Admin area.** |
| `APP_SCRYPT_N` | `65536` | Cost of the password hash. Only lower it for tests. |

## Behind a proxy

| Variable | Default | Meaning |
|---|---|---|
| `TRUST_PROXY` | `0` | `1` = believe the `X-Forwarded-For/-Proto/-Host` headers. Set it **only** behind a reverse proxy that sets them itself. |
| `COOKIE_SECURE` | `auto` | `auto` = secure cookie when the connection is HTTPS (needs `TRUST_PROXY=1` behind a proxy), `true` = always. |
| `ALLOWED_ORIGINS` | empty | Further origins (comma separated, for example `music.example.com`) allowed to send state-changing requests. Normally empty. |

## Plugins

| Variable | Default | Meaning |
|---|---|---|
| `BACKUP_PATH` (Docker) | `./backups` | A folder on the host, mounted at `/backups`: choose "Folder on the server" with the path `/backups` as the destination of a backup. Put an NFS share here (mounted on the host) to back up to a NAS. |
| `BACKUPS_ON_START` | `1` | `0` does not start the schedule of the backups to other places (tests). |
| `PLUGINS_PATH` (Docker) | `./plugins` | The folder on the host that is mounted at `/plugins`. |
| `PLUGINS_DIR` | `/plugins` in Docker | Where the server looks for plugin folders. |
| `PLUGIN_DATA_DIR` | `<data>/plugin-data` | Writable data of each plugin. |
| `PLUGIN_REPO_INSTALL` | `1` | `0` blocks installing plugins by repository address in the admin area. |
| `PLUGIN_UPLOAD` | `1` | `0` blocks installing plugins by ZIP upload. |
| `PLUGINS_ON_START` | `1` | `0` does not start plugins at startup (tests). |
| `PLUGIN_USE_VENV` | `1` | `0` runs plugins with the server's own Python instead of a virtual environment per plugin (tests). |
| `PLUGIN_HEALTH_TIMEOUT` | `90` | Seconds a plugin gets to answer after it was started. |

## Updates and warnings

| Variable | Default | Meaning |
|---|---|---|
| `UPDATE_REPO` | `nxtsound/nxtsound-server` | The GitHub repository whose newest release (or, if it has none, newest tag) counts as the newest server version. Change it only for a fork. |
| `UPDATE_CHANNEL` | `stable` | `stable` announces tested releases only; `beta` also announces pre-releases (test versions such as `0.2.0-beta.1`). This is the default; the admin's choice in Admin area → System → Updates wins. |
| `UPDATE_CHECK_HOURS` | `6` | How often the server looks for newer versions of itself and of plugins installed from a repository. `0` = never (no request to GitHub at all). |
| `DISK_WARN_GB` | `1` | Warn the admins when the free space of the data or music disk is below this. |
| `DISK_WARN_PERCENT` | `10` | ... or below this share of the disk. |
| `SYSMON_ON_START` | `1` | `0` does not start the background checks (tests). |

## Logging

Levels are `OFF`, `ERROR`, `WARNING`, `INFO`, `DEBUG`. **Admin area → System → Logging** overrides these and applies at once.

| Variable | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Default level for the server's own categories. |
| `LOG_ACCESS` | `1` | `0` turns the HTTP access log off. |
| `LOG_LEVEL_<CATEGORY>` | see below | One level per category: `SERVER`, `ACCESS`, `LIBRARY`, `ENRICH`, `PLUGINS` (managing plugins), `PLUGIN_OUTPUT` (what plugins print), `OTHER` (third-party libraries). |

Defaults: `INFO` everywhere, except `PLUGIN_OUTPUT` and `OTHER` which are `WARNING`. Logs go to standard error
(`docker compose logs server`) and the last 500 messages can be read in the admin area.

## Paths (advanced)

| Variable | Default | Meaning |
|---|---|---|
| `DB_PATH` | `/data/server.db` in Docker | The SQLite database. |
| `COVER_DIR` | `/data/covers` in Docker | Cover cache. |
| `GIT_COMMIT` | empty | Shown next to the version; the compose file passes it on as a build argument. |
