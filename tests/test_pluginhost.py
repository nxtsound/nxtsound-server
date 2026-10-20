"""Managed plugins: discovery, installation (folder / repository), start as a child process, isolation, admin API."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import main
import pluginhost
import plugins
from conftest import make_client

FIXTURE = Path(__file__).parent / "plugin_fixture"


@pytest.fixture()
def host(monkeypatch, tmp_path):
    """Empty plugins directory; after the test all child processes have ended."""
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setattr(pluginhost, "PLUGINS_DIR", d)
    monkeypatch.setattr(pluginhost, "DATA_DIR", tmp_path / "plugin-data")
    pluginhost._state.clear()
    yield d
    pluginhost.shutdown()
    for pid in list(pluginhost._state):
        pluginhost.stop(pid)


def wait_state(pid, want="running", timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        if pluginhost.status(pid)["state"] == want:
            return
        time.sleep(0.2)
    raise AssertionError(f"{pid}: {pluginhost.status(pid)}")


def put_demo(d: Path) -> Path:
    shutil.copytree(FIXTURE, d / "demo", ignore=shutil.ignore_patterns("__pycache__"))
    return d / "demo"


def test_folder_is_discovered_started_and_used(alice, host):
    put_demo(host)
    assert pluginhost.discover() == ["demo"]
    p = plugins.get("demo")
    assert p["kind"] == "managed" and p["enabled"] and p["source"] == ""
    pluginhost.start("demo")
    wait_state("demo")
    p = plugins.get("demo")
    assert p["url"].startswith("http://127.0.0.1:") and p["features"] == ["prefetch", "search", "stream"] and p["last_error"] == ""
    items = alice.get("/api/search", params={"q": "demo"}).json()["items"]
    assert items[0]["id"] == "demo:d1" and items[0]["source"] == "demo"
    s = alice.get("/api/stream/demo:d1")
    assert s.status_code == 200 and s.content == b"AUDIO" * 100


def test_plugin_gets_clean_environment_and_own_data_dir(host):
    import httpx
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    p = plugins.get("demo")
    token = plugins._token("demo")
    env = httpx.get(p["url"] + "/env", headers={"Authorization": "Bearer " + token}).json()
    assert "ADMIN_PASSWORD_SECRET_FOR_TEST" not in env["keys"] and "DB_PATH" not in env["keys"]  # no server secrets
    assert "PLUGIN_TOKEN" in env["keys"] and env["data_dir"].endswith("/demo") and env["cwd"].endswith("/demo")
    assert httpx.get(p["url"] + "/manifest").status_code == 401  # rejected without a token


def test_stop_and_disable(alice, host):
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    proc = pluginhost._state["demo"]["proc"]
    r = alice.patch("/api/admin/plugins/demo", json={"enabled": False})
    assert r.status_code == 200 and r.json()["state"] == "stopped" and proc.poll() is not None
    assert [x for x in alice.get("/api/search", params={"q": "demo"}).json()["items"] if x["id"].startswith("demo:")] == []
    alice.patch("/api/admin/plugins/demo", json={"enabled": True})
    wait_state("demo")


def test_crashed_plugin_is_restarted(host, monkeypatch):
    monkeypatch.setattr(pluginhost, "RESTART_DELAYS", (0.2, 0.2, 0.2))
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    first = pluginhost._state["demo"]["proc"].pid
    pluginhost._state["demo"]["proc"].kill()
    end = time.time() + 25
    while time.time() < end and (pluginhost.status("demo")["state"] != "running" or pluginhost._state["demo"]["proc"].pid == first):
        time.sleep(0.2)
    assert pluginhost.status("demo")["state"] == "running" and pluginhost._state["demo"]["proc"].pid != first


def test_invalid_folders_are_reported_not_started(host):
    (host / "broken").mkdir()
    (host / "broken" / "plugin.json").write_text("{not json")
    (host / "wrong").mkdir()
    (host / "wrong" / "plugin.json").write_text('{"id": "anders", "name": "N", "version": "1", "description": "D", "api_version": 1, "entry": "app:app"}')
    (host / "alt").mkdir()
    (host / "alt" / "plugin.json").write_text('{"id": "alt", "name": "N", "version": "1", "description": "D", "api_version": 9, "entry": "app:app"}')
    (host / "bose").mkdir()
    (host / "bose" / "plugin.json").write_text('{"id": "bose", "name": "N", "version": "1", "description": "D", "api_version": 1, "entry": "app:app", "requirements": "../../etc/passwd"}')
    assert pluginhost.discover() == []
    bad = pluginhost.invalid_folders()
    assert set(bad) == {"broken", "wrong", "alt", "bose"} and "must match" in bad["wrong"] and "api_version" in bad["alt"]
    assert plugins.list_plugins() == []


def test_plugin_that_does_not_start_is_marked_failed(host, monkeypatch):
    monkeypatch.setattr(pluginhost, "HEALTH_TIMEOUT", 5)
    monkeypatch.setattr(pluginhost, "RESTART_DELAYS", (30,))
    d = put_demo(host)
    (d / "app.py").write_text("raise SystemExit('broken at import')\n")
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo", "failed", 15)
    st = pluginhost.status("demo")
    assert "exited" in st["message"] and any("broken at import" in line for line in st["log"])


def _git_repo(path: Path) -> Path:
    shutil.copytree(FIXTURE, path, ignore=shutil.ignore_patterns("__pycache__"))
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


def test_install_from_repository_update_and_remove(alice, host, tmp_path, monkeypatch):
    monkeypatch.setattr(pluginhost, "ALLOW_LOCAL_REPOS", True)
    repo = _git_repo(tmp_path / "repo")
    r = alice.post("/api/admin/plugins/install", json={"repo": str(repo)})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "demo" and r.json()["source"] == str(repo) and r.json()["rev"]
    wait_state("demo")
    assert (host / "demo" / ".git").exists()
    assert alice.post("/api/admin/plugins/install", json={"repo": str(repo)}).status_code == 400  # already exists
    # new version in the repo -> the update pulls it
    (repo / "plugin.json").write_text('{"id": "demo", "name": "Demo 2", "version": "0.0.2", "description": "Demo", "api_version": 1, "entry": "app:app"}')
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "v2"], check=True, capture_output=True)
    u = alice.post("/api/admin/plugins/demo/update")
    assert u.status_code == 200 and u.json()["version"] == "0.0.2" and u.json()["name"] == "Demo 2"
    wait_state("demo")
    assert alice.delete("/api/admin/plugins/demo").status_code == 200
    assert not (host / "demo").exists() and plugins.get("demo") is None


def test_install_rejects_bad_sources_and_respects_switch(alice, host, monkeypatch):
    for bad in ("http://github.com/x/y", "git@github.com:x/y.git", "https://user:pw@github.com/x/y", "/etc/passwd", "https://github.com/../x"):
        assert alice.post("/api/admin/plugins/install", json={"repo": bad + " " * 0}).status_code in (400, 422), bad
    assert alice.post("/api/admin/plugins/install", json={"repo": "https://github.com/x/y", "ref": "a b; rm"}).status_code == 400
    monkeypatch.setattr(pluginhost, "REPO_INSTALL", False)
    r = alice.post("/api/admin/plugins/install", json={"repo": "https://github.com/x/y"})
    assert r.status_code == 400 and "turned off" in r.json()["detail"]
    assert alice.get("/api/admin/plugins").json()["repo_install"] is False


def test_hand_installed_plugin_folder_survives_remove(alice, host):
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    assert alice.delete("/api/admin/plugins/demo").status_code == 200
    assert (host / "demo").exists()  # put there by hand: the server deletes nothing
    assert "demo" in alice.get("/api/admin/plugins").json()["invalid"]


def test_plugin_install_is_admin_only(alice, host):
    bob = make_client("Bob")
    assert bob.post("/api/admin/plugins/install", json={"repo": "https://github.com/x/y"}).status_code == 403
    assert bob.post("/api/admin/plugins/demo/update").status_code == 403


def test_identifier_mismatch_names_both_identifiers(alice, host):
    d = put_demo(host)
    (d / "app.py").write_text((d / "app.py").read_text().replace('"id": "demo"', '"id": "other"'))  # manifest differs from plugin.json
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo", "failed", 20)
    msg = pluginhost.status("demo")["message"]
    assert "'other'" in msg and "'demo'" in msg and "must match" in msg


def test_managed_plugin_gets_settings_at_start_and_after_changes(alice, host):
    import httpx
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    p = plugins.get("demo")
    assert [s["key"] for s in p["settings"]] == ["ahead", "keep", "mode"] and "prefetch" in p["features"]

    def ask(path, **kw):
        return httpx.request(kw.pop("method", "GET"), p["url"] + path, headers={"Authorization": "Bearer " + plugins._token("demo")}, **kw).json()

    assert ask("/settings")["info"][0]["value"] == '{"ahead": 2, "keep": false, "mode": "a"}'  # defaults pushed after the start
    alice.put("/api/admin/plugins/demo/settings", json={"values": {"ahead": 4}})
    assert ask("/settings")["info"][0]["value"] == '{"ahead": 4, "keep": false, "mode": "a"}'
    alice.post("/api/prefetch", json={"ids": ["demo:d1", "demo:d2"]})
    assert ask("/hints")["ids"] == ["d1", "d2"]
    pluginhost.restart("demo")  # saved values are in place from the first moment of the next start
    wait_state("demo")
    p = plugins.get("demo")
    assert '"ahead": 4' in ask("/settings")["info"][0]["value"]
    assert pluginhost._child_env("demo", Path("/usr/bin/python3"), "t")["PLUGIN_IMPORT_DIR"]
