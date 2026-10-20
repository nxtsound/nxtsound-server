# Developer guide

Everything you need to understand, run, test and change the NxtSound server. If something here is wrong or missing, that is a bug in the docs: please fix it.

**Contents**
1. [The big picture](#the-big-picture)
2. [Repository layout](#repository-layout)
3. [Running from source](#running-from-source)
4. [Tests](#tests)
5. [How a request travels](#how-a-request-travels)
6. [The backend, module by module](#the-backend-module-by-module)
7. [The database](#the-database)
8. [The web app](#the-web-app)
9. [Translations](#translations)
10. [Plugins](#plugins)
11. [Background jobs](#background-jobs)
12. [The apps and the server's contract with them](#the-apps-and-the-servers-contract-with-them)
13. [Recipes: common changes](#recipes-common-changes)
14. [Conventions and rules](#conventions-and-rules)
15. [Releasing](#releasing)

---

## The big picture

```
 Browser / PWA ──┐                      ┌── music folder (read-only)
 iOS app (web view + native player) ───┤
 (Android app: wanted)                  │     FastAPI app (main.py)
                                        ▼          │
                                 ┌─────────────────┴───────────────┐
                                 │ SQLite (users, plays, playlists…)│
                                 │ cover cache                      │
                                 └─────────────────┬───────────────┘
                                                   │ HTTP + token, 127.0.0.1
                                          ┌────────┴────────┐
                                          │ plugin processes │  (radio, nextcloud, yours)
                                          └─────────────────┘
```

- **One Python process** (FastAPI/uvicorn) serves the JSON API under `/api` and the web app (static files) under `/`.
- **SQLite** in WAL mode holds everything that is not a music file. No other service is needed.
- **The web app** is plain JavaScript without a build step or a framework: edit a file, reload the page.
- **Plugins** are separate processes that speak a small HTTP protocol. The core knows no concrete service.
- **Native apps** are thin shells around the web app that add what browsers cannot do (background audio, lock screen, offline).

Design goals worth keeping in mind when you change things: easy to self-host (one container), no build tooling, readable code over clever code,
secure by default, and the core stays free of any third-party service.

## Repository layout

| Path | What it is |
|---|---|
| `main.py` | The FastAPI application: all routes, auth, security middleware, startup. |
| `db.py` | SQLite schema, migrations and every query (the only module that writes SQL for app data). |
| `library.py` | Scans the music folder, reads tags and covers, detects metadata from names, search, "similar tracks". |
| `autolists.py` | The computed "For you" lists. |
| `bpm.py` | Tempo, beat grid, key and intro/outro analysis for DJ mode (ffmpeg + numpy). |
| `enrich.py` | Year, cover and artist description from MusicBrainz and the Cover Art Archive. |
| `plugins.py` | The core's view of plugins: registry, calls to a plugin's HTTP API, ID prefixing, caching. |
| `pluginhost.py` | Managed plugins: install (git/ZIP), virtual environment, child process, restart, update check, per-plugin log. |
| `pluginproto.py` | Protocol v1: constants and manifest validation (no dependency on the rest). |
| `plugincheck.py` | Checks a running plugin against the protocol (CLI and used by tests). |
| `sysmon.py` | Background checks: disk space, newer server version (GitHub), newer plugin versions. Feeds the bell. |
| `logconf.py` | Log categories, levels (env + admin), in-memory ring buffers (global and per plugin). |
| `i18n.py` | Server-side translations (`tr()`), language resolution. |
| `static/` | The web app: `index.html`, `app.js`, `style.css`, `scrollbars.js`, `tutorial.js`, `manifest.webmanifest`, icons, `i18n/de.json`. |
| `tests/` | pytest suite (see [Tests](#tests)). `fakeplugin.py` is a stand-in plugin. |
| `examples/hello-plugin/` | A complete, runnable plugin template. |
| `docs/` | This documentation. |
| `tools/demo/` | Builds a demo instance (made-up library) and takes the README screenshots at desktop and phone size. |
| `PLUGINS.md` | The plugin protocol, reference for plugin authors. |
| `whatsnew.json` | The "What's new" cards shown once per user after an update (English and German). |
| `VERSION` | The version number. |

## Running from source

Requirements: Python 3.12, `ffmpeg` (only for the DJ analysis), `git` (to install plugins from repositories).

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
mkdir -p data music        # put a few audio files into ./music
ADMIN_USER=admin ADMIN_PASSWORD=change-me-12345 \
  .venv/bin/uvicorn main:app --reload --port 8000
```

By default the data lives in `./data` (database, covers, plugin data) and the music in `./music`. Open <http://localhost:8000>.
Because the web app has no build step, saving a file under `static/` and reloading the page is all it takes. `--reload` restarts the server when Python files change.

Useful variables while developing: `SCAN_ON_START=0`, `ANALYZE=0`, `ENRICH=0` (no background work), `LOG_LEVEL=DEBUG`.
The full list is in the [configuration reference](configuration.md).

Docker: `docker compose up --build`. The image installs `ffmpeg` and `git`, runs as user 1000 and fixes the owner of the mounted folders at startup (`docker-entrypoint.sh`).

## Tests

```sh
.venv/bin/python -m pytest -q tests          # about 30 seconds
.venv/bin/python -m pytest -q tests/test_community.py -k follow
```

How the suite is set up (`tests/conftest.py`):

- Every run gets a temporary directory for the database, music and plugins and sets the environment **before** `main` is imported. Background jobs are off (`SYSMON_ON_START=0`, `ANALYZE=0`, `ENRICH=0`, …) and there is no network.
- An autouse fixture empties the tables before each test, so tests never see each other's data. If you add a table, add it to the cleanup list there.
- Fixtures: `alice` (a logged-in **admin**), `bob` (a normal user), `anon` (not logged in), `make_client(name)` for more users, `lib` (a small library of generated WAV files, scanned). The test client sends the CSRF header for you.
- `tests/fakeplugin.py` is an in-memory plugin (`httpx.MockTransport`) used wherever a plugin is needed; `tests/test_hello_plugin.py` runs the real template against the checker.
- `tests/test_i18n.py` fails when a user-visible string has no German entry, when an entry is unused, or when placeholders differ.
- Time-dependent code takes or sets time explicitly (see `tests/test_recap.py` for helpers that insert plays at given ages).

Rules of thumb: a new endpoint gets a test for the happy path, for permission (anonymous 401, non-admin 403 where relevant) and for bad input. Run the whole suite before every commit; CI runs it for every push and pull request.

## How a request travels

1. **`SecurityMiddleware`** (pure ASGI, so audio streams without detours) runs first on every HTTP request. It resolves the language (cookie `nx_lang`, then `Accept-Language`, then `DEFAULT_LANG`),
   rejects state-changing requests from a foreign origin (403), rejects bodies over the limit (413; a counting wrapper also catches requests that do not announce their size),
   adds security headers, and sets `Cache-Control: no-store` for API answers.
2. **Routers.** `api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])` is everything behind the login; `admin = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])` is admin only.
   A handful of routes are public on purpose and registered on the app directly: `/api/server-info`, `/api/auth-state`, `/api/login`, `/api/logout`, `/api/setup`.
3. **`current_user`** reads the `session` cookie, looks the session up (the database stores only a SHA-256 hash of the cookie token), enforces the idle (`SESSION_DAYS`) and absolute (`SESSION_MAX_DAYS`) lifetime,
   and for every non-safe method requires the session's CSRF token in the `X-CSRF-Token` header. Login responses return the token; the web app keeps it in memory.
4. **Handlers** are plain functions. Blocking work (SQLite, file system) runs in a thread: `def` handlers are threaded by FastAPI, `async def` handlers call `await asyncio.to_thread(...)`.
5. **Static files** are served last (`NoCacheStatic` mounted at `/`). `index.html` is always revalidated and gets a content hash appended to its scripts and styles (`app.js?v=…`), which are then served `immutable`. That is what lets the iOS app open the page without the server.
6. **Errors.** Expected problems raise `HTTPException` with a translated message (`tr("…")`). Unexpected exceptions become a generic 500 with a reference id; the details only go to the log.

## The backend, module by module

**`main.py`** is long but flat: sections separated by `# ---------- … ----------` comments (helpers, models and auth, community, user management, search and streaming, server info, library, artists and albums,
admin, home, automatic lists, playlists, what's new, tempo analysis, lyrics, history, search history, positions, status, security middleware). Look at the section comment, not at line numbers.

Things worth knowing:
- **Track IDs** are namespaced strings: `local:<uuid5 of the relative path>` for library files and `<plugin>:<id>` for plugin tracks. A `Track` is a dict with `id, title, artist, artist_id, album, album_id, duration, thumb` (and optional `live`).
  Because IDs are derived from paths, renaming a file creates a new track; that is documented behavior.
- **Home** is built by `_build_home` and cached per user for 15 minutes, invalidated when that user plays something. The friends shelf is added outside the cache because it changes without the user playing.
- **Streaming**: library files go through `FileResponse` (Starlette handles HTTP Range, which browsers and AVPlayer need). Plugin streams are passed through with the Range header forwarded.
- **Admin dashboard, logs, backup/restore, plugin management** live in the `admin` router.

**`db.py`**: connection helper `db()` (a context manager that commits), `SCHEMA` (all `CREATE TABLE IF NOT EXISTS`), `init_db()` (applies the schema and adds columns that older databases lack), then the queries grouped by topic.
Keep SQL here and out of `main.py`. All queries are parameterized.

**`library.py`**: `scan()` walks the music folder, reads tags with `mutagen`, derives missing tags from the path with the rules in the user guide, extracts embedded or folder covers, and updates `lib_tracks` (incrementally by mtime and size; `full=True` re-reads everything).
Search is word-prefix based, ignores case and accents.

**`autolists.py`** computes lists on request from plays, likes and the library. Nothing is stored. "Mixes stay stable per day" works by seeding the shuffle with user, list and date.

**`bpm.py`**: decodes audio with ffmpeg, estimates tempo and beat grid with numpy, derives the Camelot key, intro end and outro start. Results are cached in the `bpm` table. `ANALYZE=1` queues new tracks after each scan, one at a time.

**`enrich.py`**: rate-limited (one request per second) MusicBrainz and Cover Art Archive lookups. It only fills gaps and remembers misses (retried after 30 days).

**`sysmon.py`**: `loop()` runs checks every 30 minutes (disks) and every `UPDATE_CHECK_HOURS` (versions). Results become notifications for admins and disappear when the cause is gone.
The server version comes from `latest_release(repo)`: the newest GitHub release, or the newest tag if there is no release.

**`logconf.py`**: maps logger names to categories, resolves levels (admin setting, then `LOG_LEVEL_<CATEGORY>`, then `LOG_LEVEL`, then default), keeps ring buffers. Plugin output is captured per plugin.

**`i18n.py`**: `tr(msg, **values)` looks the English text up in the catalog of the request's language and formats `{placeholders}`. `trn(n, one, many)` picks singular or plural.

## The database

SQLite file at `DB_PATH`, WAL mode, foreign keys on (deleting a user cascades).

| Table | Holds |
|---|---|
| `users` | name, password hash (scrypt), `is_admin`, language, onboarding and "what's new" state, `notif_seen`, `share_listening` |
| `sessions` | hashed cookie token, user, CSRF token, created and last-seen time |
| `lib_tracks` | the library index (path, tags, duration, mtime/size, …) |
| `meta_artist`, `meta_album` | enrichment results (including misses) |
| `plugins` | registered plugins (address, token, enabled, settings, last error) |
| `playlists`, `playlist_tracks` | playlists and their entries (track data is stored with the entry) |
| `likes`, `dislikes` | liked tracks; "stop recommending" for tracks and artists |
| `plays` | listening history (one row per play, with the track data) |
| `listening` | seconds listened per user and day |
| `positions` | resume positions for long tracks |
| `searches`, `search_picks` | search history and picked results |
| `follows` | community: who follows whom |
| `notifications` | bell messages: `target` 0 = system (admins), else a user id; unique per `(target, key)` |
| `settings` | key/value settings (admin choices, community switch, cached update answer, …) |
| `stream_stats` | how long songs took to start (health card) |
| `lyrics`, `bpm` | caches |

**Migrations**: there is no migration framework. `SCHEMA` creates missing tables; `init_db()` checks `PRAGMA table_info` and `ALTER TABLE … ADD COLUMN` for columns added later.
To add a column: add it to `SCHEMA` for new databases **and** to the list in `init_db()` for existing ones, with a safe default. Destructive migrations should be avoided; if one is unavoidable, make a copy of the old data first.

**Backup/restore** exports a consistent copy of the database; `restore` validates it (a real NxtSound database, at least one admin), keeps a `before-restore-*.db`, ends all sessions and rescans.

## The web app

One page, no framework, no build: `static/index.html` + `app.js` (about 3,000 lines) + `style.css`. Read `app.js` top to bottom once; it is organized in labeled sections.

- **State** lives in one object `S` (user, CSRF token, queue, index, likes, settings…). Player state is also saved to `localStorage` so a reload resumes where you were.
- **Helpers**: `$()` for `querySelector`, `esc()` to escape HTML (use it for every dynamic value!), `tr()`/`trn()` for translations, `api(path, opts)` for calls (adds the CSRF header, handles 401 by showing the login, times out).
- **Routing** is hash based (`#/home`, `#/artist/<id>`, `#/admin/plugins`, …). `route()` picks a view function. Each view is `async function viewX(my, …)`; `my` is the navigation counter at the time of the call and every `await` is followed by `if (my !== S.nav) return;` so a slow response never paints over a newer page.
- **Rendering** is string templates assigned to `innerHTML`. Click handling is delegated: elements carry `data-act="name"` and the handler lives in the `actions` object; list items carry `data-l` and `data-i` (list key and index) and `setList(key, tracks)` registers the tracks so a click can play them.
- **Player**: one `<audio id="audio">`, plus a second for DJ crossfades. Queue, shuffle, repeat, preloading of the next track, media session, and a self-healing ladder for dead audio sessions on iOS.
- **Native bridge**: when running inside the iOS app, a user script replaces the audio element's behavior and forwards it to the native player (`window.__nativeAudioEvent`, `__nativeAudioAdvanced`). The web code stays the single source of truth for the queue.
- **Admin screens** (`adminOverview`, `adminUsers`, `adminPlugins`, `adminSystem`) are ordinary views that call `/api/admin/*`.
- **Styling**: CSS variables for the theme, mobile first; the desktop layout adds a sidebar. `scrollbars.js` draws overlay scroll bars for mouse devices only.

When you add UI: escape everything, add `tr()` to every visible string, test on a narrow viewport (phone) and a wide one, and check keyboard use.

## Translations

Source strings in code are **English**. Python: `tr("Text {name}", name=x)`; JavaScript: `tr('Text {name}', { name: x })`; static HTML: `data-i18n="Text"` (also `data-i18n-ph`, `data-i18n-aria`, `data-i18n-title`).
German goes into `static/i18n/de.json` as `"English text": "Deutscher Text"`. The same file serves the server and the web app. A missing entry falls back to English at runtime, but `tests/test_i18n.py` fails, so you will not forget one.

**Adding a language** (for example French):
1. Add the code to `LANGS` in `i18n.py`.
2. Add the option to the language selector in the account view in `static/app.js` (search for `lang` in `viewAccount`), plus `detectLang` if the browser language should map to it.
3. Copy `static/i18n/de.json` to `static/i18n/fr.json` and translate the values. Keep the `{placeholders}` exactly.
4. Extend `tests/test_i18n.py` if it iterates over languages by name. Run the tests.

The iOS app has its own `Localizable.strings` files in its repository.

## Plugins

The full protocol is in [`PLUGINS.md`](../PLUGINS.md) (for authors). From the core's point of view:

- A plugin is a **web service** with a manifest (`GET /manifest`: id, name, version, description, features, optional `settings`, `actions`, `categories`) and endpoints for search, stream, radio, artist, album, suggest, now-playing, prefetch.
- `plugins.py` keeps the registry in the `plugins` table, calls plugins with `Authorization: Bearer <token>` and `X-NxtSound-Api: 1`, prefixes plugin IDs with `<id>:` and merges results with the library.
- `pluginhost.py` makes this convenient: a folder in `plugins/<id>/` with `plugin.json` is set up with its own virtual environment (`pip install -r requirements.txt`) and started as a child process on `127.0.0.1` with a random token, restarted after crashes.
  Install paths: by hand, by repository (`git clone`), by ZIP (checked: size, no absolute or `..` paths, no symlinks).
- **Updates**: for plugins installed from a repository the host runs `git fetch` and compares `HEAD` with its upstream (cached in `UPDATES`); `sysmon` turns "newer exists" into a notification.
- **Per-plugin log**: the plugin's stdout/stderr and the server's lines about it are kept in a ring buffer per plugin.
- **Security model**: a plugin is trusted code with the rights of the server, but it only receives its own token and folders in its environment, never the server's secrets. Installing is admin-only and can be disabled.
- **The core must stay generic.** No code, tests, docs or UI text may mention a specific external service. Use neutral names like `demo` in tests.

To write a plugin: copy `examples/hello-plugin/`, follow its README, and check it with `python plugincheck.py http://localhost:8100 --token secret`.

## Background jobs

All started in the FastAPI lifespan (`main.py`), all switchable by environment variables for tests:

| Job | Does | Switch |
|---|---|---|
| Library scan | at startup and every `SCAN_MINUTES` | `SCAN_ON_START`, `SCAN_MINUTES` |
| DJ analysis | after each scan, one track at a time | `ANALYZE` |
| Enrichment | after each scan, one request per second | `ENRICH` |
| Plugin host | starts managed plugins, restarts crashed ones | `PLUGINS_ON_START` |
| System monitor | disks every 30 min, versions every `UPDATE_CHECK_HOURS` | `SYSMON_ON_START`, `UPDATE_CHECK_HOURS` |

Jobs must never crash the server: catch, log, carry on.

## The apps and the server's contract with them

The native apps rely on a small, stable surface:

- `GET /api/server-info` (public): `{"server": "nxtsound", "name", "version", "api_version", "features", …}`. An app links only to servers that answer with `"server": "nxtsound"` and an `api_version` it supports. **Changing the shape of this endpoint is a breaking change**; bump `api_version` for incompatible API changes.
- Cookie session login (`POST /api/login`), CSRF header for writes, `GET /api/stream/<id>` with Range support for the native player.
- `GET /api/me/offline-set?recent=20&top=20`: the songs an app may keep for offline use (last played and most played, each once; only things that exist and are real files, no live streams, no disliked songs).
- The page must keep working when served from the app's cache (see `NoCacheStatic`) and must show a friendly screen when the server cannot be reached.

Want to write an **Android app**? The iOS app is the blueprint: a web view that shows the server's web app, a native media player (Media3/ExoPlayer) that replaces the page's audio element through a script bridge,
media session notifications, and a download manager for the offline set. Open an issue to coordinate.

## Recipes: common changes

**Add an API endpoint**
1. Write the query in `db.py` (parameterized, with a docstring).
2. Add the route in `main.py` in the matching section: `@api.get("/…")` for logged-in users, `@admin.get("/…")` for admins. Validate input with Pydantic models or `Query(...)` constraints. Raise `HTTPException` with `tr("…")` messages.
3. Test it: happy path, 401 anonymous, 403 for non-admin if admin, 422/400 for bad input.
4. Add German text to `de.json`.

**Add a setting** (environment variable): read it once at import (`os.getenv("NAME", "default")`), document it in `docs/configuration.md`, `env.example`, and pass it through `docker-compose.yml`. If tests would be affected, set it in `tests/conftest.py`.

**Add a table or column**: see [The database](#the-database). Add the table to the cleanup list in `tests/conftest.py`.

**Add a notification kind**: call `db.notify(target, key, kind, data)` where the event happens (the key makes it unique and lets you resolve it later with `db.resolve_notification`),
then add the rendering for the kind in `_render_notification` in `main.py` (title, body, where a click goes). Texts are rendered at read time in the reader's language.

**Add a view to the web app**: write `async function viewX(my, …)`, add a `case` in `route()`, link to it with `#/x`, and, if it needs a menu entry, add it to `index.html` (side and bottom navigation).

**Change the plugin protocol**: only additive changes within version 1 (new optional fields and endpoints). Update `pluginproto.py`, `PLUGINS.md`, `plugincheck.py`, `tests/fakeplugin.py` and the hello plugin together.

## Conventions and rules

- Code, comments, documentation and commit messages are **English**. The user interface is English with German translations.
- **No service-specific code in the core.** Anything about one particular external service belongs in a plugin in its own repository.
- **Security first**: escape output (`esc()`), parameterize SQL, never build file paths from user input without checking, never log passwords or tokens, validate sizes. Read [SECURITY.md](../SECURITY.md).
- Prefer small, readable functions; comment the *why*, not the *what*. Match the style of the file you edit. There is no formatter configured; keep lines reasonable and follow what is there.
- Tests for every change in behavior; the suite must be green.
- Commit messages: a short imperative summary line (`Add follow button to the community page`), then a body that explains why when it is not obvious.
- `CLAUDE.md` is a compact summary of these rules for AI coding assistants and a handy cheat sheet for people.

## Releasing

Maintainers only.

1. Make sure `main` is green in CI.
2. Update `VERSION`, add a section to `CHANGELOG.md`, and add a card to `whatsnew.json` if users should hear about it (English `title`/`text`, German `title_de`/`text_de`; cards show once per user after the update).
3. Commit, then tag: `git tag v0.2.0 && git push origin main --tags`.
4. The **Release workflow** builds and publishes the Docker image (`ghcr.io/nxtsound/nxtsound-server`, tags `0.2.0`, `0.2`, `latest`).
5. On GitHub, create a **release** from the tag (Releases → Draft a new release, "generate release notes", edit, publish).
   Servers learn about the new version from the newest release, or from the newest tag if there is no release; marking a release makes it clear which version is stable.
6. Announce it where users are.

Versioning: `MAJOR.MINOR.PATCH`. While the version starts with `0.`, minor versions may contain breaking changes; they are listed in the changelog.
