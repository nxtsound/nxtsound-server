# CLAUDE.md – NxtSound server

Guidance for AI coding tools (and a good summary for human contributors). Personal preferences belong in `CLAUDE.local.md` (not committed).

## Project
Self-hosted music server with a web UI (PWA): library scan, search, playlists, recommendations, DJ analysis, metadata enrichment, backup/restore. The display name comes from `APP_NAME` / `/api/server-info`; never hardcode it. Further sources are **plugins**. The iOS app is a separate repo (`../nxtsound-ios`).

## Conventions
- Code, comments, docs and commit messages are **English**. The UI and server messages are English and German.
- **Translations:** every user-visible string is written in English and wrapped in `tr("…")` (Python) or `tr('…')` (JS); static HTML uses `data-i18n*` attributes. Add the German text to `static/i18n/de.json`; `tests/test_i18n.py` fails otherwise and checks placeholders. Logs and debug output stay English and untranslated.
- Tests: `.venv/bin/python -m pytest -q tests` (temporary DB; fixtures `alice` = admin, `bob`, `lib` = a small library of generated WAV files). Run them before every commit.
- No secrets in the repo; never log user data or credentials.

## Architecture rules
1. **No service-specific code in the core.** Anything that belongs to one particular external service (code, tests, docs, UI texts) goes into a plugin in its own repository. The core only knows the plugin protocol.
2. Plugins are **separate processes** speaking an HTTP protocol (`PLUGINS.md`, checker `plugincheck.py`, stand-in `tests/fakeplugin.py`, template `examples/hello-plugin/`): managed by the server (`pluginhost.py`: folder `plugins/<id>/` with `plugin.json`, own venv, child process; installed by hand or via repository URL) or registered as remote. The core never imports plugin code. Only the admin installs plugins; `PLUGIN_REPO_INSTALL=0` blocks installation by URL.
3. `GET /api/server-info` is public and carries the marker `"server": "nxtsound"` (name, version, API version, features, login methods); the iOS app relies on it.
4. Track IDs are namespaced: `local:<uuid>` (uuid5 of the relative path), `<plugin>:<id>` for plugin tracks.
