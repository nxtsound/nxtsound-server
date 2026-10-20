"""Managed plugins: a folder in the plugins directory that the server sets up and starts itself.

Installation either by hand (put a folder with `plugin.json` into the plugins directory, e.g. `git clone`) or in the admin area
via repository URL. Either way the plugin gets its own virtual environment (`.venv`) with its dependencies and runs as a child
process of the server on 127.0.0.1 with a random token. The same HTTP protocol as for remote plugins is spoken (PLUGINS.md);
the core never imports plugin code.

plugin.json:  {"id": "meine-quelle", "name": "Meine Quelle", "version": "1.0.0", "api_version": 1,
               "entry": "app:app", "requirements": "requirements.txt"}
"""
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path, PurePosixPath

import db
import library
import logconf
import pluginproto
import plugins
from i18n import tr
from pluginproto import API_VERSION, ID_RE, PluginError

log = logging.getLogger("pluginhost")

BASE = Path(__file__).parent
PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", BASE / "plugins"))
DATA_DIR = Path(os.getenv("PLUGIN_DATA_DIR", db.DB_PATH.parent / "plugin-data"))
USE_VENV = os.getenv("PLUGIN_USE_VENV", "1") != "0"  # 0 only for tests: the plugin then runs with the server's Python
REPO_INSTALL = os.getenv("PLUGIN_REPO_INSTALL", "1") != "0"  # 0 = installation by URL in the admin area is blocked
UPLOAD_INSTALL = os.getenv("PLUGIN_UPLOAD", "1") != "0"  # 0 = installation by ZIP upload in the admin area is blocked
MAX_ZIP_BYTES = 50 << 20  # largest accepted upload
MAX_UNPACKED_BYTES = 300 << 20  # largest accepted size after unpacking (zip bomb guard)
MAX_ZIP_FILES = 10000
INSTALL_TIMEOUT = 900
HEALTH_TIMEOUT = float(os.getenv("PLUGIN_HEALTH_TIMEOUT", "90"))
RESTART_DELAYS = (2, 5, 15, 60, 120)

ENTRY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
REPO_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")

_state: dict[str, dict] = {}
_invalid: dict[str, PluginError] = {}  # folder name -> why it is not a valid plugin
_lock = threading.RLock()


# ---------- plugin.json ----------

def read_spec(folder: Path) -> dict:
    try:
        spec = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PluginError("plugin.json is missing or not valid JSON") from e
    if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not ID_RE.match(spec["id"]) or spec["id"] == "local":
        raise PluginError("plugin.json: 'id' is missing or invalid (a-z, 0-9, hyphen, 2-24 characters, not 'local')")
    if spec.get("api_version") != API_VERSION:
        raise PluginError("plugin.json: api_version {got} (the server understands {want})", got=spec.get("api_version"), want=API_VERSION)
    if not isinstance(spec.get("entry"), str) or not ENTRY_RE.match(spec["entry"]):
        raise PluginError("plugin.json: 'entry' must look like 'module:app'")
    req = spec.get("requirements")
    if req is not None and (not isinstance(req, str) or "/" in req or req.startswith(".") or not (folder / req).is_file()):
        raise PluginError("plugin.json: 'requirements' must be a file in the plugin folder")
    pluginproto.require_identity(spec, "plugin.json")
    return {"id": spec["id"], "name": spec["name"].strip()[:60], "version": spec["version"].strip()[:40],
            "description": spec["description"].strip()[:300], "entry": spec["entry"], "requirements": req}


def dir_problem() -> str:
    """Why the plugins directory cannot be written to ('' if it can). Installing plugins needs to write there."""
    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        probe = PLUGINS_DIR / f".write-test-{secrets.token_hex(4)}"
        probe.write_text("x")
        probe.unlink()
        return ""
    except OSError as e:
        return tr("Cannot write to the plugins folder {path}: {why}. Check its owner and permissions (inside the container the server runs as user ID 1000).",
                  path=str(PLUGINS_DIR), why=e.strerror or str(e))


def _folder(plugin_id: str) -> Path:
    return PLUGINS_DIR / plugin_id


# ---------- State ----------

def _st(plugin_id: str) -> dict:
    with _lock:
        return _state.setdefault(plugin_id, {"status": "stopped", "message": ("", {}), "log": deque(maxlen=80), "proc": None, "stop": threading.Event(), "thread": None})


def _set(plugin_id: str, status: str, message: str = "", **values) -> None:
    """Sets the state. `message` is an English template stored untranslated; `status()` translates it for the reader."""
    s = _st(plugin_id)
    s["status"], s["message"] = status, (message, values)


def _failed(plugin_id: str, e: Exception) -> None:
    if isinstance(e, PluginError):
        _set(plugin_id, "failed", e.msg, **e.values)
    else:
        _set(plugin_id, "failed", "{reason}", reason=str(e)[:200])


def status(plugin_id: str) -> dict:
    s = _st(plugin_id)
    msg, values = s["message"]
    return {"state": s["status"], "message": tr(msg, **values) if msg else "", "log": list(s["log"])[-15:]}


def invalid_folders() -> dict[str, str]:
    return {name: e.localized() for name, e in _invalid.items()}


# ---------- Setting up and starting ----------

def _run(cmd: list[str], s: dict, timeout: float, cwd: Path | None = None, env: dict | None = None) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise PluginError("{tool}: {reason}", tool=Path(cmd[0]).name, reason=str(e)) from e
    for line in (r.stdout + r.stderr).splitlines()[-20:]:
        s["log"].append(line[:300])
    if r.returncode != 0:
        raise PluginError("{reason}", reason=(r.stderr.strip().splitlines() or [f"{Path(cmd[0]).name} failed"])[-1][:300])


def _python_for(plugin_id: str, folder: Path, spec: dict) -> Path:
    """Virtual environment with the plugin's dependencies (rebuilt only when the requirements change)."""
    if not USE_VENV:
        return Path(sys.executable)
    s = _st(plugin_id)
    venv = folder / ".venv"
    py = venv / "bin" / "python"
    req = (folder / spec["requirements"]) if spec.get("requirements") else None
    want = hashlib.sha256((req.read_bytes() if req else b"") + b"|uvicorn").hexdigest()
    stamp = venv / ".nxtsound-deps"
    if py.exists() and stamp.exists() and stamp.read_text() == want:
        return py
    _set(plugin_id, "installing", "Setting up the environment (the first time can take a few minutes) …")
    shutil.rmtree(venv, ignore_errors=True)
    _run([sys.executable, "-m", "venv", str(venv)], s, 120)
    pip = [str(py), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-q"]
    _run(pip + ["uvicorn[standard]"] + (["-r", str(req)] if req else []), s, INSTALL_TIMEOUT)
    stamp.write_text(want)
    return py


def _free_port() -> int:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def _child_env(plugin_id: str, py: Path, token: str) -> dict:
    data = DATA_DIR / plugin_id
    (data / "cache").mkdir(parents=True, exist_ok=True)
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}  # do not pass server secrets (admin password etc.) on to plugins
    env["PATH"] = f"{py.parent}{os.pathsep}{env.get('PATH', '')}" if USE_VENV else env.get("PATH", "")
    env.update(PLUGIN_TOKEN=token, PLUGIN_DATA_DIR=str(data), CACHE_DIR=str(data / "cache"), PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    mine = library.import_root() / plugin_id  # the plugin's own folder: files it puts here join the library (see PLUGINS.md)
    try:
        mine.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("Plugin %s: cannot create its library folder %s: %s", plugin_id, mine, e)
    env["PLUGIN_IMPORT_DIR"] = str(mine)
    p = plugins.get(plugin_id)
    if p and p["settings"]:
        env["PLUGIN_SETTINGS"] = json.dumps(plugins.settings_values(p))  # the plugin has its settings from its first moment on
    return env


def _pump(proc: subprocess.Popen, s: dict, plugin_id: str = "") -> None:
    plog = logging.getLogger(f"plugin.{plugin_id}")  # category "Plugin output"; level adjustable (see logconf.py)
    for line in proc.stdout:  # type: ignore[union-attr]
        s["log"].append(line.rstrip()[:300])
        logconf.plugin_line(plugin_id, line.rstrip()[:500])  # for the plugin's own log view (independent of the log level)
        plog.info("%s", line.rstrip()[:500])


def _wait_healthy(plugin_id: str, port: int, token: str, proc: subprocess.Popen) -> None:
    import httpx
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            raise PluginError("The process has exited")
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/manifest", headers={"Authorization": f"Bearer {token}", "X-NxtSound-Api": str(API_VERSION)}, timeout=2)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    raise PluginError("The plugin does not respond")


def _supervise(plugin_id: str) -> None:
    s = _st(plugin_id)
    stop: threading.Event = s["stop"]
    attempt = 0
    while not stop.is_set():
        started = time.time()
        try:
            folder = _folder(plugin_id)
            spec = read_spec(folder)
            py = _python_for(plugin_id, folder, spec)
            port, token = _free_port(), secrets.token_urlsafe(24)
            _set(plugin_id, "starting", "")
            proc = subprocess.Popen([str(py), "-m", "uvicorn", spec["entry"], "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                                    cwd=folder, env=_child_env(plugin_id, py, token), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            s["proc"] = proc
            threading.Thread(target=_pump, args=(proc, s, plugin_id), daemon=True, name=f"plugin-log-{plugin_id}").start()
            log.debug("Plugin %s started as pid %d, waiting for it to answer", plugin_id, proc.pid)
            _wait_healthy(plugin_id, port, token, proc)
            plugins.set_endpoint(plugin_id, f"http://127.0.0.1:{port}", token)
            p = plugins.refresh(plugin_id)
            if p and p["last_error"]:
                raise PluginError("{reason}", reason=p["last_error"])
            _set(plugin_id, "running", "")
            log.info("Plugin %s running on port %d", plugin_id, port)
            proc.wait()
        except PluginError as e:
            _failed(plugin_id, e)
            proc = s.get("proc")
            if proc and proc.poll() is None:
                proc.kill()
        except Exception as e:  # noqa: BLE001 (the supervisor must never die)
            log.exception("Plugin %s", plugin_id)
            _failed(plugin_id, e)
        if stop.is_set():
            break
        attempt = 0 if time.time() - started > 600 else attempt + 1
        delay = RESTART_DELAYS[min(attempt - 1, len(RESTART_DELAYS) - 1)] if attempt else RESTART_DELAYS[0]
        if attempt > len(RESTART_DELAYS) + 3:
            msg, values = s["message"]
            _set(plugin_id, "failed", "{reason} (gave up, restart it in the admin area)", reason=msg.format(**values) if msg else "Crashes repeatedly")
            break
        log.warning("Plugin %s exited, restarting in %ds", plugin_id, delay)
        stop.wait(delay)
    if _st(plugin_id)["status"] not in ("failed",):
        _set(plugin_id, "stopped", "")


def start(plugin_id: str) -> None:
    """Starts (and if needed sets up) the plugin in the background; a running supervisor is not started twice."""
    s = _st(plugin_id)
    with _lock:
        t = s.get("thread")
        if t and t.is_alive():
            return
        s["stop"] = threading.Event()
        _set(plugin_id, "starting", "")
        s["thread"] = threading.Thread(target=_supervise, args=(plugin_id,), daemon=True, name=f"plugin-{plugin_id}")
        s["thread"].start()


def stop(plugin_id: str, wait: bool = True) -> None:
    s = _st(plugin_id)
    s["stop"].set()
    proc = s.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    t = s.get("thread")
    if wait and t and t.is_alive() and t is not threading.current_thread():
        t.join(15)
    _set(plugin_id, "stopped", "")


def restart(plugin_id: str) -> None:
    stop(plugin_id)
    start(plugin_id)


def shutdown() -> None:
    for pid in list(_state):
        stop(pid, wait=False)


# ---------- Discovery and installation ----------

def discover() -> list[str]:
    """Registers all valid plugin folders in the plugins directory (new ones are active at once; whoever can put files on the server is the admin)."""
    found: list[str] = []
    _invalid.clear()
    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        folders = sorted(PLUGINS_DIR.iterdir())
    except OSError as e:
        log.warning("Plugins directory %s cannot be read: %s", PLUGINS_DIR, e)
        return found
    for folder in folders:
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        try:
            spec = read_spec(folder)
            if spec["id"] != folder.name:
                raise PluginError("The folder is named '{folder}' but the identifier in plugin.json is '{id}' (they must match)", folder=folder.name, id=spec["id"])
        except PluginError as e:
            _invalid[folder.name] = e
            continue
        if not plugins.get(spec["id"]):
            plugins.register_managed(spec, source="", rev=_git_rev(folder))
        found.append(spec["id"])
    return found


def start_all() -> None:
    """At server start: discover folders and start all active managed plugins."""
    for pid in discover():
        p = plugins.get(pid)
        if p and p["enabled"] and p["kind"] == "managed":
            start(pid)


def _git_rev(folder: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(folder), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


ALLOW_LOCAL_REPOS = False  # tests only: local path instead of an https address


def _valid_repo(url: str) -> bool:
    return ALLOW_LOCAL_REPOS or (bool(REPO_RE.match(url)) and ".." not in url)


def install_repo(url: str, ref: str = "") -> dict:
    """Fetches a plugin from a repository via git (https), puts it into the plugins directory and starts it."""
    if not REPO_INSTALL:
        raise PluginError("Installation by repository address is turned off on this server.")
    url = url.strip().removesuffix("/")
    if not _valid_repo(url):
        raise PluginError("The repository address must start with https:// and must not contain credentials.")
    if ref and not REF_RE.match(ref):
        raise PluginError("Invalid branch or tag.")
    if (problem := dir_problem()):
        raise PluginError("{reason}", reason=problem)
    tmp = PLUGINS_DIR / f".neu-{secrets.token_hex(4)}"
    s = {"log": deque(maxlen=20)}
    try:
        cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", f"protocol.file.allow={'always' if ALLOW_LOCAL_REPOS else 'never'}",
               "clone", "--depth", "1"] + (["--branch", ref] if ref else []) + ["--", url, str(tmp)]
        _run(cmd, s, 180, env={**{k: os.environ[k] for k in ENV_KEEP if k in os.environ}, "GIT_TERMINAL_PROMPT": "0"})
        spec = read_spec(tmp)
        if plugins.get(spec["id"]) or _folder(spec["id"]).exists():
            raise PluginError("A plugin with the identifier '{id}' already exists.", id=spec["id"])
        rev = _git_rev(tmp)
        tmp.rename(_folder(spec["id"]))
    except PluginError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    plugins.register_managed(spec, source=url, rev=rev)
    log.info("Plugin %s installed from %s", spec["id"], url)
    start(spec["id"])
    return plugins.get(spec["id"])


SKIPPED_DIRS = {".venv", ".git", "__pycache__"}  # never trusted, never needed: left out when unpacking (a zipped project folder often contains them)


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Unpacks a ZIP into `dest` after checking sizes and paths (no absolute paths, no `..`, no symbolic links).
    Environments, repositories and bytecode caches inside the archive are ignored entirely."""
    import zipfile
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise PluginError("The file is not a valid ZIP archive.") from e
    with z:
        infos = [i for i in z.infolist() if not SKIPPED_DIRS & set(PurePosixPath(i.filename).parts)]
        if len(infos) > MAX_ZIP_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
            raise PluginError("The archive is too large.")
        root = dest.resolve()
        for i in infos:
            parts = PurePosixPath(i.filename).parts
            if i.filename.startswith(("/", "\\")) or "\\" in i.filename or ".." in parts or (parts and re.match(r"^[A-Za-z]:", parts[0])):
                raise PluginError("The archive contains unsafe paths.")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise PluginError("The archive contains symbolic links.")
            if not (root / i.filename).resolve().is_relative_to(root):
                raise PluginError("The archive contains unsafe paths.")
        try:
            z.extractall(dest, members=infos)
        except (zipfile.BadZipFile, OSError, RuntimeError) as e:
            raise PluginError("The file is not a valid ZIP archive.") from e


def _plugin_root(dest: Path) -> Path:
    """The folder with plugin.json: the archive root, or its single top-level folder (like GitHub's "Download ZIP")."""
    if (dest / "plugin.json").is_file():
        return dest
    dirs = [d for d in dest.iterdir() if d.is_dir() and d.name != "__MACOSX" and not d.name.startswith(".")]
    if len(dirs) == 1 and (dirs[0] / "plugin.json").is_file():
        return dirs[0]
    raise PluginError("plugin.json was not found in the archive (it belongs into the top folder).")


def install_zip(zip_path: Path, replace: str = "") -> dict:
    """Installs a plugin from an uploaded ZIP. With `replace` (the plugin ID) a plugin installed by upload is replaced by a new version."""
    if not UPLOAD_INSTALL:
        raise PluginError("Installation by ZIP upload is turned off on this server.")
    if (problem := dir_problem()):
        raise PluginError("{reason}", reason=problem)
    tmp = PLUGINS_DIR / f".upload-{secrets.token_hex(4)}"
    try:
        tmp.mkdir()
        _safe_extract(zip_path, tmp)
        root = _plugin_root(tmp)
        spec = read_spec(root)
        pid, existing = spec["id"], plugins.get(spec["id"])
        if replace:
            if replace != pid:
                raise PluginError("The archive contains the plugin '{new}', not '{old}'.", new=pid, old=replace)
            if not existing or existing["kind"] != "managed" or existing["source"] != "upload":
                raise PluginError("Only plugins installed by ZIP upload can be replaced by an upload.")
        elif existing or _folder(pid).exists():
            raise PluginError("A plugin with the identifier '{id}' already exists.", id=pid)
        for junk in (".venv", ".git"):  # never trust environments or repositories inside an archive
            shutil.rmtree(root / junk, ignore_errors=True)
        folder = _folder(pid)
        if replace:
            stop(pid)
            old_env = folder / ".venv"
            if old_env.exists():  # keep the environment; it is rebuilt only if the requirements changed
                old_env.rename(root / ".venv")
            shutil.rmtree(folder, ignore_errors=True)
        shutil.move(str(root), str(folder))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if replace:
        plugins.set_meta(pid, spec, "")
        log.info("Plugin %s replaced by an uploaded version %s", pid, spec["version"])
    else:
        plugins.register_managed(spec, source="upload", rev="")
        log.info("Plugin %s installed from an uploaded ZIP (version %s)", pid, spec["version"])
    start(pid)
    return plugins.get(pid)


UPDATES: dict[str, dict] = {}  # plugin ID -> result of the last check for a newer version (see check_update)
CHANGES_SHOWN = 30


def _git_out(folder: Path, *args: str, timeout: float = 60) -> str:
    """Runs a git command in a plugin's repository (https only, never asks for credentials) and returns its output."""
    cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", f"protocol.file.allow={'always' if ALLOW_LOCAL_REPOS else 'never'}", "-C", str(folder), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**{k: os.environ[k] for k in ENV_KEEP if k in os.environ}, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError) as e:
        raise PluginError("git: {reason}", reason=str(e)) from e
    if r.returncode != 0:
        raise PluginError("git: {reason}", reason=((r.stderr or r.stdout).strip().splitlines() or ["failed"])[-1][:200])
    return r.stdout.strip()


def check_update(plugin_id: str) -> dict:
    """Is there a newer version in the repository the plugin was installed from? Fetches (changes nothing in the plugin itself) and compares.

    -> {"supported": False} for plugins that are not tracked by a repository branch (uploaded, installed by hand, pinned to a tag);
       otherwise {"supported": True, "available", "current", "latest", "version", "changes": [subjects of the new commits], "checked"}."""
    p = plugins.get(plugin_id)
    folder = _folder(plugin_id)
    if not p or p["kind"] != "managed" or p["source"] in ("", "upload") or not (folder / ".git").exists():
        return {"supported": False}
    try:
        upstream = _git_out(folder, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")  # e.g. origin/main; fails on a detached HEAD (a tag)
    except PluginError:
        return {"supported": False}
    _git_out(folder, "fetch", "--depth", "50", "origin", timeout=120)
    current, latest = _git_out(folder, "rev-parse", "HEAD"), _git_out(folder, "rev-parse", "@{u}")
    out = {"supported": True, "available": current != latest, "current": current[:7], "latest": latest[:7], "version": "", "changes": [], "checked": time.time(), "upstream": upstream}
    if out["available"]:
        out["changes"] = [x.strip()[:200] for x in _git_out(folder, "log", "--format=%s", f"-n{CHANGES_SHOWN}", "HEAD..@{u}").splitlines() if x.strip()]
        try:
            out["version"] = str(json.loads(_git_out(folder, "show", "@{u}:plugin.json")).get("version") or "")[:40]
        except (PluginError, ValueError):
            pass
    UPDATES[plugin_id] = out
    return out


def check_all_updates() -> dict[str, dict]:
    """Checks every plugin that came from a repository. A failing check (no network, repository gone) is skipped and does not hide an earlier result."""
    for p in plugins.list_plugins():
        if p["kind"] == "managed" and p["source"] not in ("", "upload"):
            try:
                check_update(p["id"])
            except PluginError as e:
                log.info("Plugin %s: update check failed: %s", p["id"], e)
    for gone in [k for k in UPDATES if not plugins.get(k)]:
        UPDATES.pop(gone, None)
    return dict(UPDATES)


def update(plugin_id: str) -> dict:
    p = plugins.get(plugin_id)
    folder = _folder(plugin_id)
    if not p or p["kind"] != "managed":
        raise PluginError("Plugin not found")
    if p["source"] == "upload":
        raise PluginError("This plugin was uploaded as a ZIP; upload the new version instead.")
    if not (folder / ".git").exists():
        raise PluginError("This plugin was installed by hand; update the folder on the server.")
    _run(["git", "-C", str(folder), "pull", "--ff-only"], _st(plugin_id), 120, env={**{k: os.environ[k] for k in ENV_KEEP if k in os.environ}, "GIT_TERMINAL_PROMPT": "0"})
    try:
        plugins.set_meta(plugin_id, read_spec(folder), _git_rev(folder))
    except PluginError as e:
        raise PluginError("Invalid after the update: {reason}", reason=e.localized()) from e
    UPDATES.pop(plugin_id, None)
    db.resolve_notification(db.SYSTEM, f"plugin_update:{plugin_id}")
    restart(plugin_id)
    return plugins.get(plugin_id)


def remove(plugin_id: str) -> None:
    """Stops the plugin and unregisters it. The folder is only deleted if the server installed it by address."""
    p = plugins.get(plugin_id)
    if not p or p["kind"] != "managed":
        raise PluginError("Plugin not found")
    stop(plugin_id)
    if p["source"]:
        shutil.rmtree(_folder(plugin_id), ignore_errors=True)
        shutil.rmtree(DATA_DIR / plugin_id, ignore_errors=True)
    plugins.remove(plugin_id)
    _state.pop(plugin_id, None)
    UPDATES.pop(plugin_id, None)
    logconf.forget_plugin(plugin_id)
    db.resolve_notification(db.SYSTEM, f"plugin_update:{plugin_id}")
    if not p["source"]:
        _invalid[plugin_id] = PluginError("Removed, but the folder is still on the server (delete it, otherwise it is registered again at the next start)")
