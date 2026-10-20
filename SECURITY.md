# Security

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private reporting
(Security tab → "Report a vulnerability") and describe what you found and how to reproduce it. You will get an answer as soon as
possible; a fix is released before details are published.

## Supported versions

Only the latest release receives security fixes.

## What the server protects against, and what it does not

- Accounts are created by the admin; passwords are stored as salted scrypt hashes; sessions expire (`SESSION_DAYS`, at most `SESSION_MAX_DAYS`).
- State-changing requests need a CSRF token and a matching origin; request bodies are size-limited.
- Plugins are separate processes that run **with the rights of the server's user**: only install plugins you trust. Installing and
  uploading plugins is limited to admins and can be switched off (`PLUGIN_REPO_INSTALL=0`, `PLUGIN_UPLOAD=0`).
- Run the server behind a reverse proxy with HTTPS if it is reachable from the internet, and set `TRUST_PROXY=1` only then.
