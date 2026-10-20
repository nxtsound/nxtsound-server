# Hello plugin: template for your own NxtSound plugins

A complete, runnable mini plugin (three test songs made of generated tones). Copy the folder as a starting point for your own plugin.
The protocol is described in `../../PLUGINS.md`.

## Try it

```sh
cp -r examples/hello-plugin plugins/hello     # in the server's folder (Docker: ./plugins)
# restart the server (or check/restart it in the admin area under System → Plugins)
```

Afterwards searching for "tone" returns three hits from "Hello-Plugin"; radio, artist and album pages, search suggestions and playback work. In the admin area the plugin shows its name, version and
description; the gear button opens its settings.

## Build your own plugin

1. Copy and rename the folder. **The folder name must equal the `id` in `plugin.json`** (a-z, 0-9, hyphen, 2–24 characters).
2. In `plugin.json` adjust `id`, `name`, `description` and `version` (all four are **required**; the manifest in `app.py` reads them from this file). `entry` is the ASGI app (`module:object`), `requirements` the dependencies (the server installs uvicorn itself).
3. In `app.py` fill the functions with your source. At least `search` and `stream`; `radio`, `artist`, `album`, `suggest` are optional but are
   only called if they appear under `features` in the manifest.
4. IDs are your own (at most 40 characters, `A-Z a-z 0-9 _ . -`). Images only as `https://` addresses.
5. Your plugin only gets `PLUGIN_TOKEN`, `PLUGIN_DATA_DIR` (own writable folder), `CACHE_DIR`, `PLUGIN_SETTINGS` and `PLUGIN_IMPORT_DIR`. No server secrets.
6. **Settings** (optional): list them under `settings` in `plugin.json` (types `bool`, `int`, `choice`; texts may be `{"en": …, "de": …}`). The admin gets a gear button on the plugin's card;
   the server stores the values and sends them to your plugin (`PLUGIN_SETTINGS` at start, `PUT /settings` after every change). This example has two: capital letters and the number of
   songs per search. Delete the `settings` list and the two `/settings` functions if your plugin needs none.

## Test

During development without the server: start the plugin and check it against the protocol with the checker from the server repo.

```sh
python3 -m venv .venv && .venv/bin/pip install fastapi "uvicorn[standard]"
PLUGIN_TOKEN=secret .venv/bin/uvicorn app:app --port 8100 &
python ../../plugincheck.py http://localhost:8100 --token secret --query tone
```

For automatic tests in your own repo: copy `pluginproto.py` and `plugincheck.py` from the server repo into your test directory
(both only need `httpx`) and call `plugincheck.check(...)`. The server tests this example the same way (`tests/test_hello_plugin.py`).

## Publish

Put the plugin into a repository of its own (`plugin.json` in the root folder). Admins then install it in the admin area via repository address
or by hand with `git clone … plugins/<id>`.
