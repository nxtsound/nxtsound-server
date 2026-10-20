# Contributing to NxtSound

Thank you for wanting to help! NxtSound is a small project, and every kind of contribution matters: code, plugins, translations, documentation,
screenshots, bug reports, and good ideas.

## Ways to contribute

| You like … | You could … |
|---|---|
| Kotlin / Android | Build the **Android app** (see below). This is the biggest gap. |
| Python | Fix bugs and add features in this repository; write a **plugin** for a music source. |
| JavaScript / CSS | Improve the web app (it is plain JS, no build step). |
| Languages | Translate the app into your language (one JSON file, no code). |
| Writing | Improve the [user guide](docs/user-guide.md), the [developer guide](docs/developer-guide.md), or add a FAQ entry. |
| Testing | Try the alpha features, report what breaks, say what is confusing. |
| Design | Take screenshots (`docs/screenshots/`), suggest improvements. |

## Android app

The iOS app is a thin native shell around the web app. It adds what a browser cannot do well: reliable background playback, lock screen and
notification controls, offline songs. An Android app can follow the same idea:

- a web view that shows the server's web app (after asking for the server address once and checking `GET /api/server-info`),
- a native player (Media3 / ExoPlayer) in a foreground service that replaces the page's `<audio>` element through a small script bridge,
- media session controls, and a download manager for `GET /api/me/offline-set`.

The server side is ready: the API version in `/api/server-info`, cookie login, Range streaming, the offline set. If you want to lead this,
**open an issue titled "Android app"** and say what you plan; we will agree on a repository (`nxtsound-android`), the architecture and milestones together,
and I will help with anything the server needs. You do not have to finish it alone; a first prototype that plays music is already a great start.

## Before you start

- For anything bigger than a small fix, **open an issue first** so we agree on the direction before you spend time.
- Read the [developer guide](docs/developer-guide.md); it explains the code layout, how to run it and how to test.
- Please keep the project's rules in mind:
  - Code, comments, documentation and commit messages are **English**. User-visible texts are English with a German translation in `static/i18n/de.json`.
  - **No service-specific code in the core.** Support for an external service is a plugin in its own repository ([plugin guide](PLUGINS.md)).
  - Security matters: escape output, parameterize SQL, no secrets in the repo, never log passwords. See [SECURITY.md](SECURITY.md).

## How to contribute code, step by step

1. **Fork** the repository on GitHub and clone your fork.
2. **Create a branch**: `git switch -c fix-queue-reorder`.
3. **Set up**:
   ```sh
   python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
   ```
   (`ffmpeg` is needed for the DJ analysis tests.)
4. **Make your change.** Keep it focused: one topic per pull request. Add or update tests; add German text for new strings.
5. **Run the tests**: `.venv/bin/python -m pytest -q tests`. They must pass.
6. **Try it** in the browser, on a narrow (phone) and a wide screen, if you touched the web app.
7. **Commit** with a clear message: a short imperative summary (`Fix drag handle on touch screens`), a blank line, and why the change is needed if it is not obvious.
8. **Push** to your fork and **open a pull request**. Describe what and why; add screenshots for visible changes. CI runs the tests automatically.
9. Reviewers may ask for changes. That is normal and friendly. Push more commits to the same branch.

## Writing a plugin

A plugin lives in its own repository and speaks a small HTTP protocol. Start from [`examples/hello-plugin/`](examples/hello-plugin) and the [plugin guide](PLUGINS.md).
Name it `nxtsound-plugin-<name>`, give it a `plugin.json`, a license and a README with install instructions, and test it with `plugincheck.py`.
Once it works, you can add it to [docs/community-plugins.md](docs/community-plugins.md) with a pull request (the page lists what we accept).

## Translations

Copy `static/i18n/de.json` to `static/i18n/<code>.json`, translate the values (keep `{placeholders}`), and add the language as described in the
[developer guide](docs/developer-guide.md#translations). You can do most of it without running any code.

## Reporting bugs

Use the bug report template. The most helpful reports contain: the server version (bottom of the home page), how you run it (Docker or not, behind a proxy?),
steps to reproduce, and the relevant log lines (Admin area → System → Logging → "Show recent messages"). Remove anything private first.
**Security problems:** do not open a public issue, see [SECURITY.md](SECURITY.md).

## Code of conduct

Be kind and assume good intent. Disagree with ideas, not with people. Harassment and discrimination are not tolerated. Maintainers may remove comments or contributions and block accounts that break these rules.

## License

By contributing you agree that your contribution is licensed under the [MIT license](LICENSE) of this project.
