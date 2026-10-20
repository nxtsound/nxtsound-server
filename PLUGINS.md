# Plugins (protocol version 1)

Further music sources are **plugins**: their own repo, their own dependencies, their own process. The core knows no concrete
service and never loads plugin code into itself; it talks to every plugin through the HTTP protocol below and passes on search,
radio, stream as well as artist and album pages. Only the admin installs and removes plugins.

## Installation

**On the server (recommended):** the server manages the plugin itself. It stores it as a folder in the plugins directory
(`PLUGINS_DIR`, in the Docker setup `./plugins`), sets up its own virtual environment with the plugin's dependencies and starts it
as a child process on `127.0.0.1` with a random token (including restart after crashes). Three ways lead there:

1. **By hand** (not via the admin area): put the plugin folder into `plugins/<id>/`, e.g. `git clone https://… plugins/<id>`, restart the server.
2. **In the admin area** (Plugins): enter the repository address (https); the server fetches it with `git clone`.
   `PLUGIN_REPO_INSTALL=0` blocks this way (then only the by-hand way remains).

Every plugin brings a `plugin.json` in its root folder:

```json
{"id": "my-source", "name": "My Source", "version": "1.0.0", "api_version": 1,
 "description": "What this source offers, in one sentence.",
 "entry": "app:app", "requirements": "requirements.txt"}
```

`name`, `description` and `version` are **required** (non-empty): the admin area shows all three. `entry` is an ASGI app (`module:object`, started with uvicorn; the server installs uvicorn itself), `requirements` is optional. The folder name must equal the `id`.
The plugin only receives `PLUGIN_TOKEN` (access token), `PLUGIN_DATA_DIR` and `CACHE_DIR` (its own data folder under the server's data directory),
`PLUGIN_SETTINGS` (its saved settings as JSON, see below), `PLUGIN_IMPORT_DIR` (see "Keeping songs in the library") as well as
`PATH`, `HOME` and proxy/certificate settings; no secrets of the server. The server waits until `GET /manifest` answers.

**Remote (separate service):** a plugin can also run somewhere else (another machine, its own container). Admin area → "Register a remote plugin" with
address and token. The protocol is the same.

Security note: during setup the server runs the installation of the dependencies (pip) and the plugin code. Whoever may install plugins can thereby run code on the
server. Hence: admins only, only sources you trust, and on servers that give strangers access to the admin area set `PLUGIN_REPO_INSTALL=0`.

Check whether a plugin speaks the protocol:

```sh
python plugincheck.py http://localhost:8100 --token SECRET --query "search term"
```

**Template to get started:** `examples/hello-plugin/` (a complete, commented mini plugin; instructions in its README). `tests/fakeplugin.py` is a stand-in of the protocol for tests.
Plugins can copy `pluginproto.py` and `plugincheck.py` (both free of dependencies on the rest of the server, only `httpx`) into their tests and thus check against the protocol automatically.

## Ground rules

- The core calls the plugin over HTTP on its base address. Every request carries
  `Authorization: Bearer <token>` (if a token is registered) and `X-NxtSound-Api: 1`.
  A plugin with a token **must** reject requests without a valid token with `401` or `403`.
- Answers are JSON (except `/stream`). Errors: the matching HTTP status; unknown IDs → `404`.
- Time limit of the core: 10 s per request (streams: 10 s until the first byte). Slow sources cache by themselves.
- **IDs**: the plugin delivers and expects its *own* IDs (`A-Z a-z 0-9 _ . -`, at most 40 characters). The core prefixes `<plugin-id>:`
  (`id`, `artist_id`, `album_id`) when handing them to the UI and removes it again before asking the plugin.
- **Images** (`thumb`) are absolute `https://` addresses or empty.
- The core does not store audio files of plugins; caches belong in the plugin. A plugin that wants to *keep* songs puts them into the import folder
  (see "Keeping songs in the library").
- Everything service-specific (accounts, cookies, download tools, legal questions) lives in the plugin, never in the core.
- **Language**: the core translates its own messages; texts coming from a plugin (titles, descriptions) are shown as delivered.

## Endpoints

### `GET /manifest`
```json
{"id": "my-source", "name": "My Source", "version": "1.0.0", "api_version": 1,
 "description": "Short description",
 "features": ["search", "stream", "radio", "artist", "album"],
 "search_kinds": ["songs", "artists", "albums"],
 "categories": [],
 "settings": []}
```
`id`: `a-z`, `0-9`, `-`, 2–24 characters, starting with a letter, not `local`. `name`, `description` and `version` are required, non-empty texts (a manifest without
them is rejected; the same goes for `plugin.json`). `api_version` must be `1`. `features` ⊆ {search, stream, radio, artist, album, suggest, prefetch, nowplaying},
non-empty; only what is announced gets called. `settings` is optional, see below.

Tip: read `id`, `name`, `version` and `description` for the manifest from your `plugin.json` instead of repeating them in code. Then renaming the plugin means
editing one file, and the manifest can never disagree with it (the server refuses a plugin whose manifest `id` differs from `plugin.json`).

### `GET /search?q=…&kind=songs|artists|albums|<category>&limit=25` (feature `search`)
`{"items": [...]}` – depending on `kind`:
- songs: `{"id", "title", "artist", "artist_id", "album", "album_id", "duration" ("3:30"), "thumb", "live"}` (`id` and `title` required). `live: true` marks a stream
  without an end (a radio station): the player then shows "Live" instead of a progress bar. Without the flag the player still notices it, a few seconds into playback, when neither the track
  nor the audio has a length.
- artists: `{"type": "artist", "id", "name", "thumb"}`
- albums: `{"type": "album", "id", "title", "artist", "year", "kind", "thumb"}`

**Own search tabs (categories):** besides songs, artists and albums a plugin can add tabs to the search, e.g. "Live radio" or "Podcasts". Declare them in the manifest:

```json
"search_kinds": [],
"categories": [{"id": "radio", "label": {"en": "Live radio", "de": "Live-Radio"}}]
```

`id`: `a-z`, `0-9`, `_`, 2–24 characters, not `songs`, `artists` or `albums`; at most 4 per plugin; `label` is a text as for settings. The tab appears as soon as the plugin is active. When the user opens it,
the server calls `GET /search?q=…&kind=<id>` on the plugins that declare that category and expects the same answer as for `songs` (track-shaped items: stations, episodes, …).
Results show up **only in that tab**, never in "Songs", so list `songs` under `search_kinds` only if your plugin also has real songs (an empty `search_kinds` is fine). Plugins that use the same `id` share one tab.

### `GET /suggest?q=…` (feature `suggest`, optional)
`{"suggestions": ["text", …]}` – search suggestions while the user types (at most 8 texts). Must answer quickly (the server waits at most 2.5 s);
slow sources should rather return nothing than too late.

### `GET /nowplaying/{id}` (feature `nowplaying`, optional)
`{"title": "Song", "artist": "Artist"}` – the song a **live stream** plays right now (e.g. read from the station's stream metadata); `{}` or empty texts if unknown. The player asks about every
20 seconds while a live track plays, and shows it as "Now playing" (also on the lock screen). The server reuses an answer for 10 s and waits at most 5 s; answer from a cache if the source is slow.

### `GET /radio/{id}?limit=40` (feature `radio`)
`{"tracks": [<track as for songs>]}` – tracks similar to the seed track.

### `GET /artist/{id}` (feature `artist`)
`{"id", "name", "description", "subscribers", "thumb", "songs": [<track>], "albums": [<album cards without type>], "singles": [...]}`

### `GET /album/{id}` (feature `album`)
`{"id", "title", "kind", "year", "duration", "thumb", "artist", "artist_id", "tracks": [<track>]}`

### `GET /stream/{id}` (feature `stream`)
Audio bytes with `Content-Type: audio/*`. **Range requests** (`Range: bytes=a-b`) are answered with `206` and `Content-Range`
(seeking and iOS playback need that). Unknown ID → `404`.

**Live streams** (radio stations) have no end and no Range support: answer `200` with the stream (chunked, no `Content-Length`) and a real `audio/*` type (set it yourself from the codec if the station sends something else:
the server rejects anything else). Pass the bytes on as they arrive and close the connection to the source when the client goes away. If the source does not answer in time, return `502` quickly.

### `POST /prefetch` (feature `prefetch`, optional)
Body `{"ids": ["id1", "id2", …]}` (your own IDs, at most 20): these tracks are queued next, in playing order. The server sends the hint a few seconds after a track
starts. Answer quickly with `200` or `202` and do the work in the background; take only as many as your own settings allow. Best effort: the hint may be repeated
or never come, and playing must work without it.

## Settings

A plugin can offer settings the admin changes in the admin area (gear button on the plugin's card). They are described in the manifest:

```json
"settings": [
  {"key": "ahead", "type": "int", "label": {"en": "Songs to load ahead", "de": "Titel im Voraus laden"},
   "help": {"en": "…", "de": "…"}, "min": 0, "max": 10, "unit": {"en": "songs", "de": "Titel"}, "default": 2},
  {"key": "auto_update", "type": "bool", "label": "Update automatically", "default": true},
  {"key": "channel", "type": "choice", "label": "Channel", "default": "stable", "depends_on": "auto_update",
   "choices": [{"value": "stable", "label": "Stable"}, {"value": "nightly", "label": "Nightly"}]}
]
```

- `type`: `bool`, `int` (needs `min` and `max`, optional `unit`), `choice` (needs `choices`, each with `value` and `label`) or `text` (optional `max_length`, default 200, at most 400, `placeholder` and `secret`; the default may be `""`). `default` is required (except for `text`) and must be valid.
- Texts (`label`, `help`, `unit`, choice `label`) are either a plain English string or `{"en": "…", "de": "…"}`; the admin sees their language, English is the fallback.
- `key`: `a-z`, `0-9`, `_`, starting with a letter. `depends_on` names an *earlier* `bool` setting: the setting is only shown while that one is on.
- The server validates every change against the schema (invalid input is rejected), stores the values and sends them to the plugin. The plugin never has to store them itself.

A `text` setting with `"secret": true` is for passwords and tokens: the admin area shows a password field, never hands the saved value out again (the sheet only learns that one is saved) and
keeps it when the field is left empty on save. The value still reaches the plugin (as everything else) and lives in the server's database, hence in its backups; use app passwords or scoped tokens where the service offers them.

How the plugin gets its values:

1. **At start** (managed plugins): environment variable `PLUGIN_SETTINGS`, a JSON object with every key (defaults filled in).
2. **After every change** and whenever the server reads the manifest: `PUT /settings` with `{"values": {…}}`, always the complete set. Apply them at once (no restart) and
   answer `200` with an optional `{"info": […]}`.
3. `GET /settings` returns the read-only `info` the admin area shows below the form: `{"info": [{"label": "Version", "value": "1.2.3"}]}` (`label` is a text as above, `value` a string, a number, or `{"en": "…", "de": "…"}` for a text per language).
   Typical: the version of a tool the plugin uses, when it last updated, cache size.

## Actions

An action is something the admin can *do* with the plugin from the gear button, with a small form: add a station to the source, clear a cache, test a connection. Declare them in the manifest:

```json
"actions": [
  {"id": "add_station", "label": {"en": "Add a station", "de": "Sender hinzufügen"}, "help": "…",
   "fields": [{"key": "name", "type": "text", "required": true, "max_length": 100, "label": "Name"},
              {"key": "tags", "type": "text", "label": "Tags"}]}
]
```

- `fields` are the same entries as for settings (all four types). On a `text` field `required: true` makes the form refuse an empty value. Nothing is stored by the server; the values go to the plugin once.
- `id`: `a-z`, `0-9`, `_`, 2–32 characters; at most 4 actions per plugin. A plugin with actions but no settings still gets the gear button.
- The server validates the values against the fields (an invalid form never reaches the plugin), trims text and fills in defaults for missing keys, then calls
  `POST /actions/{id}` with `{"values": {…}}` (time limit 25 s).
- Answer `200` with `{"ok": true|false, "message": "text for the admin"}` (`message` may also be `{"en": "…", "de": "…"}`): a failure the source reports belongs in `ok: false` with a message, not in an HTTP error.
  The message is shown under the form and the form stays open, so several can be run one after the other. Only admins can run actions.

## Keeping songs in the library

Every managed plugin gets a folder of its own for songs it keeps, in `PLUGIN_IMPORT_DIR`: `<music folder>/<plugin-id>/` if the music folder is mounted read-write
(`MUSIC_ACCESS=rw` in the Docker setup), otherwise `<data>/imported/<plugin-id>/` (`IMPORT_DIR`, a second library folder that is always writable). Write only inside it.
Everything a plugin puts there is part of the library (the plugin's folder name itself is not read as an artist or album): the server notices changes within a minute and scans it like the music folder
(file and folder names are read the same way: `Artist - Title.ext` or `Artist/Album/Title.ext`). Use it for an optional "keep the songs I listen to" mode and make it a setting
(see above); a plugin that only caches simply does not write there. Write files completely before they appear under their final name (write to a name starting with `.`, then rename).

## Versioning

`api_version` counts breaking changes of the protocol. The core rejects plugins with a different version (clear message in the admin area). New optional fields in
answers are not a break; the core ignores unknown ones. The same holds for optional features and manifest fields such as `prefetch` and `settings`: a plugin that does
not know them keeps working, and the server only calls what a plugin announces.

## Security

- The registered token is stored in the server's database (and therefore in the backup) and is never returned by the API.
- A plugin runs in its own process (own environment, no server secrets in the environment). It is still third-party code with the rights of the server user
  (in the Docker setup: inside the container). Trusted sources only.
- Managed plugins listen on `127.0.0.1` only. Remote plugins should run in a private network and not be reachable publicly.
