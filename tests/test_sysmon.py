"""Checks of the server itself: disk space, a new server version on GitHub, new plugin versions, the plugin's own log."""
import collections
import shutil
import subprocess
import time

import httpx
import pytest

import db
import logconf
import main
import pluginhost
import plugins
import sysmon
from conftest import make_client
from test_pluginhost import _git_repo, host, wait_state  # noqa: F401 (host is a fixture)

GIB = 1024**3


def system_keys():
    return [i["key"] for i in db.notifications_for(1, True)["items"] if i["system"]]


@pytest.fixture()
def admin(alice):
    return alice


# ---------- Disk space ----------

def test_low_disk_space_warns_and_clears_with_a_margin(admin, monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    state = {"free": 0.5 * GIB}
    monkeypatch.setattr(sysmon.shutil, "disk_usage", lambda p: usage(100 * GIB, 100 * GIB - state["free"], state["free"]))
    monkeypatch.setattr(sysmon, "volumes", lambda: [("data", "/data")])
    assert sysmon.check_disks()[0]["name"] == "data" and system_keys() == ["disk_low:data"]
    n = admin.get("/api/notifications").json()["items"][0]
    assert n["title"] == "Disk space is running low" and n["body"] == "Data folder: 0.5 GB of 100.0 GB free (0 %)"
    state["free"] = 11 * GIB  # a bit above the limit (10 %): still flagged, so it does not flicker
    sysmon.check_disks()
    assert system_keys() == ["disk_low:data"]
    state["free"] = 40 * GIB
    assert sysmon.check_disks() == [] and system_keys() == []


def test_a_percentage_below_the_limit_warns_even_with_gigabytes_free(admin, monkeypatch):
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(sysmon.shutil, "disk_usage", lambda p: usage(1000 * GIB, 950 * GIB, 50 * GIB))  # 5 %
    monkeypatch.setattr(sysmon, "volumes", lambda: [("music", "/music")])
    assert sysmon.check_disks() and system_keys() == ["disk_low:music"]


def test_one_disk_is_looked_at_once(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.setattr(db, "DB_PATH", a / "x.db")
    monkeypatch.setattr(sysmon.library, "MUSIC_DIR", b)
    assert [n for n, _ in sysmon.volumes()] == ["data"]  # both folders are on the same disk


# ---------- Server version ----------

def github(release: dict | None = None, tags: list | None = None, status: int = 200, releases: list | None = None):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        assert req.headers["user-agent"] == "nxtsound-server"
        if req.url.path.endswith("/releases") and releases is not None:  # the list (beta channel)
            return httpx.Response(status, json=releases)
        if req.url.path.endswith("/releases/latest"):
            return httpx.Response(status, json=release) if release is not None else httpx.Response(404 if status == 200 else status)
        if req.url.path.endswith("/tags"):
            return httpx.Response(200, json=tags or [])
        return httpx.Response(404)

    t = httpx.MockTransport(handler)
    t.calls = calls  # type: ignore[attr-defined]
    return t


@pytest.fixture()
def repo(monkeypatch):
    monkeypatch.setattr(sysmon, "UPDATE_REPO", "nxtsound/nxtsound-server")
    monkeypatch.setattr(sysmon, "UPDATE_CHANNEL", "stable")
    monkeypatch.setattr(sysmon, "_state", {"server": None, "plugins_checked": 0.0, "server_checked": 0.0, "server_error": ""})
    yield
    sysmon.set_transport(None)


def test_version_numbers_are_compared_by_their_numbers():
    v = sysmon.version_tuple
    assert v("v0.2.10") > v("0.2.9") > v("0.2") and v("1.0.0-rc1") == (1, 0, 0) and v("2026.10.01") > v("2026.9.30") and v("junk") == (0,)


def test_versions_are_ordered_like_semantic_versioning():
    k = sysmon.version_key
    order = ["0.1.0", "0.2.0-alpha", "0.2.0-beta.1", "0.2.0-beta.2", "0.2.0-beta.10", "0.2.0-rc.1", "0.2.0", "0.2.1", "0.10.0", "1.0.0"]
    assert [x for x in sorted(reversed(order), key=k)] == order
    assert k("v0.2") == k("0.2.0") and k("0.2.0+build5") == k("0.2.0") and k("junk") < k("0.0.1")
    assert sysmon.is_prerelease("0.2.0-beta.1") and not sysmon.is_prerelease("0.2.0") and not sysmon.is_prerelease("v1")


def test_a_newer_release_is_announced_to_admins(admin, repo):
    sysmon.set_transport(github({"tag_name": "v0.9.0", "body": "## Changes\n- faster", "html_url": "https://github.com/nxtsound/nxtsound-server/releases/tag/v0.9.0"}))
    out = sysmon.check_server_update()
    assert out["available"] and out["latest"] == "0.9.0" and "faster" in out["notes"] and out["current"] == main.VERSION_INFO["version"]
    n = admin.get("/api/notifications").json()["items"][0]
    assert n["title"] == "Server update available: 0.9.0" and n["system"] is True
    snap = admin.get("/api/admin/updates").json()["server"]
    assert snap["available"] and snap["enabled"] and snap["url"].endswith("v0.9.0") and "faster" in snap["notes"]


def test_no_message_when_up_to_date_and_an_old_one_goes_away(admin, repo):
    sysmon.set_transport(github({"tag_name": "v9.0.0", "body": "", "html_url": "https://x"}))
    sysmon.check_server_update()
    assert system_keys() == ["server_update"]
    sysmon.set_transport(github({"tag_name": "v" + main.VERSION_INFO["version"], "body": "", "html_url": "https://x"}))  # installed in the meantime
    assert sysmon.check_server_update()["available"] is False and system_keys() == []


def test_tags_are_used_when_there_is_no_release(admin, repo):
    sysmon.set_transport(github(release=None, tags=[{"name": "v0.3.0"}, {"name": "v0.10.0"}, {"name": "nightly"}]))
    out = sysmon.check_server_update()
    assert out["latest"] == "0.10.0" and out["available"]  # numerically the highest, not alphabetically


def test_without_a_repository_nothing_is_asked(admin, monkeypatch):
    monkeypatch.setattr(sysmon, "UPDATE_REPO", "")
    sysmon.set_transport(github({"tag_name": "v9"}))
    out = sysmon.check_server_update()
    assert out["enabled"] is False and out["available"] is False and system_keys() == []
    monkeypatch.setattr(sysmon, "UPDATE_REPO", "not a repo!")  # invalid names are never put into an address
    assert sysmon.check_server_update()["enabled"] is False


def test_github_errors_are_survived_and_the_last_answer_stays(admin, repo):
    sysmon.set_transport(github({"tag_name": "v9.0.0", "body": "x", "html_url": "https://x"}))
    sysmon.check_server_update()
    sysmon.set_transport(github(release={}, status=403))  # rate limited
    out = sysmon.check_server_update()
    assert out["latest"] == "9.0.0" and sysmon.snapshot()["server"]["error"] == "HTTPStatusError" and system_keys() == ["server_update"]


def test_the_last_answer_survives_a_restart(admin, repo):
    sysmon.set_transport(github({"tag_name": "v5.0.0", "body": "notes", "html_url": "https://x"}))
    sysmon.check_server_update()
    sysmon._state["server"] = None  # as after a restart
    assert sysmon.snapshot()["server"]["latest"] == "5.0.0" and sysmon.snapshot()["server"]["notes"] == "notes"


def test_checks_can_be_started_by_the_admin_and_not_by_users(admin, bob, repo):
    sysmon.set_transport(github({"tag_name": "v9.0.0", "body": "", "html_url": "https://x"}))
    assert bob.post("/api/admin/updates/check").status_code == 403 and bob.get("/api/admin/updates").status_code == 403
    r = admin.post("/api/admin/updates/check")
    assert r.status_code == 200 and r.json()["server"]["available"] is True and r.json()["check_hours"] == sysmon.UPDATE_CHECK_HOURS


# ---------- Plugins from a repository ----------

def commit(repo_path, subject: str, version: str):
    (repo_path / "plugin.json").write_text('{"id": "demo", "name": "Demo", "version": "%s", "description": "Demo", "api_version": 1, "entry": "app:app"}' % version)
    subprocess.run(["git", "-C", str(repo_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", subject], check=True, capture_output=True)


def test_a_newer_plugin_version_is_found_listed_and_installed(admin, host, tmp_path, monkeypatch):
    monkeypatch.setattr(pluginhost, "ALLOW_LOCAL_REPOS", True)
    pluginhost.UPDATES.clear()
    origin = _git_repo(tmp_path / "origin")
    assert admin.post("/api/admin/plugins/install", json={"repo": str(origin)}).status_code == 200
    wait_state("demo")
    assert admin.post("/api/admin/plugins/demo/check-update").json()["available"] is False  # nothing new yet
    commit(origin, "Faster search", "0.0.2")
    commit(origin, "Fix the login", "0.0.3")
    info = admin.post("/api/admin/plugins/demo/check-update").json()
    assert info["supported"] and info["available"] and info["version"] == "0.0.3" and info["changes"] == ["Fix the login", "Faster search"]
    assert "plugin_update:demo" in system_keys()
    listed = next(p for p in admin.get("/api/admin/plugins").json()["plugins"] if p["id"] == "demo")
    assert listed["update"]["available"] and listed["update"]["changes"][0] == "Fix the login"  # the card knows it without asking again
    n = admin.get("/api/notifications").json()["items"][0]
    assert n["title"] == "Plugin update available: Demo-Plugin" and n["body"] == "Version 0.0.3"
    assert admin.post("/api/admin/plugins/demo/update").status_code == 200  # installing clears the message and the result
    wait_state("demo")
    assert "plugin_update:demo" not in system_keys() and "demo" not in pluginhost.UPDATES
    assert admin.post("/api/admin/plugins/demo/check-update").json()["available"] is False


def test_the_background_check_covers_all_repository_plugins(admin, host, tmp_path, monkeypatch):
    monkeypatch.setattr(pluginhost, "ALLOW_LOCAL_REPOS", True)
    pluginhost.UPDATES.clear()
    origin = _git_repo(tmp_path / "origin")
    admin.post("/api/admin/plugins/install", json={"repo": str(origin)})
    wait_state("demo")
    commit(origin, "New", "0.0.2")
    monkeypatch.setattr(sysmon, "UPDATE_REPO", "")
    sysmon.run_checks(updates=True)
    assert "plugin_update:demo" in system_keys()
    shutil.rmtree(origin)  # the repository is gone: the check fails quietly and the earlier result stays
    sysmon.run_checks(updates=True)
    assert "plugin_update:demo" in system_keys() and pluginhost.UPDATES["demo"]["available"]


def test_plugins_without_a_repository_have_no_updates(admin, host, monkeypatch):
    import shutil as sh
    from test_pluginhost import put_demo
    pluginhost.UPDATES.clear()
    put_demo(host)
    pluginhost.discover()
    assert pluginhost.check_update("demo") == {"supported": False}  # installed by hand
    p = next(p for p in admin.get("/api/admin/plugins").json()["plugins"] if p["id"] == "demo")
    assert p["update"] == {"supported": False}
    assert admin.post("/api/admin/plugins/nope/check-update").status_code == 404
    assert sh  # (imported for symmetry with the other tests)


# ---------- The plugin's own log ----------

def test_a_plugin_has_its_own_log(admin, host, monkeypatch):
    from test_pluginhost import put_demo
    logconf._plugin_buffers.clear()
    put_demo(host)
    pluginhost.discover()
    pluginhost.start("demo")
    wait_state("demo")
    lines = admin.get("/api/admin/plugins/demo/log").json()["lines"]
    assert any("Plugin demo running" in l["message"] for l in lines)  # what the server says about it
    logconf.plugin_line("demo", "hello from the plugin")
    logconf.plugin_line("other", "not mine")
    mine = [l["message"] for l in admin.get("/api/admin/plugins/demo/log").json()["lines"]]
    assert "hello from the plugin" in mine and "not mine" not in mine
    assert admin.get("/api/admin/plugins/nope/log").status_code == 404 and make_client("Bob").get("/api/admin/plugins/demo/log").status_code == 403


def test_plugin_output_is_kept_whatever_the_log_level(admin, monkeypatch):
    logconf._plugin_buffers.clear()
    monkeypatch.setenv("LOG_LEVEL_PLUGIN_OUTPUT", "OFF")
    logconf.apply()
    logconf.plugin_line("demo", "still there")
    assert [l["message"] for l in logconf.recent_for_plugin("demo")] == ["still there"]
    monkeypatch.delenv("LOG_LEVEL_PLUGIN_OUTPUT")
    logconf.apply()


def test_the_plugin_log_is_limited_and_forgotten_on_removal(admin):
    logconf._plugin_buffers.clear()
    for i in range(logconf.PLUGIN_BUFFER_SIZE + 50):
        logconf.plugin_line("demo", f"line {i}")
    lines = logconf.recent_for_plugin("demo", 1000)
    assert len(lines) == logconf.PLUGIN_BUFFER_SIZE and lines[-1]["message"] == f"line {logconf.PLUGIN_BUFFER_SIZE + 49}"
    logconf.forget_plugin("demo")
    assert logconf.recent_for_plugin("demo") == []


def test_default_repository_is_the_public_project():
    assert sysmon.DEFAULT_REPO == "nxtsound/nxtsound-server" and sysmon.REPO_RE.match(sysmon.DEFAULT_REPO)


# ---------- Channels: stable and beta ----------

RELEASES = [
    {"tag_name": "v0.2.0-beta.2", "prerelease": True, "draft": False, "body": "beta two", "html_url": "https://x/beta2"},
    {"tag_name": "v0.2.0-beta.1", "prerelease": True, "draft": False, "body": "beta one", "html_url": "https://x/beta1"},
    {"tag_name": "v0.1.1", "prerelease": False, "draft": False, "body": "stable", "html_url": "https://x/011"},
    {"tag_name": "v0.3.0", "prerelease": False, "draft": True, "body": "not published yet", "html_url": "https://x/draft"},
]


def test_stable_is_the_default_and_ignores_pre_releases(admin, repo):
    assert sysmon.channel() == "stable"
    t = github({"tag_name": "v0.1.1", "body": "stable", "html_url": "https://x/011", "prerelease": False}, releases=RELEASES)
    sysmon.set_transport(t)
    out = sysmon.check_server_update()
    assert out["latest"] == "0.1.1" and out["channel"] == "stable" and out["prerelease"] is False
    assert any(c.endswith("/releases/latest") for c in t.calls) and not any(c.split("?")[0].endswith("/releases") for c in t.calls)


def test_beta_takes_the_highest_version_including_pre_releases_but_no_drafts(admin, repo, monkeypatch):
    monkeypatch.setattr(main, "VERSION_INFO", {"version": "0.1.1", "commit": ""})
    sysmon.set_transport(github(releases=RELEASES))
    sysmon.set_channel("beta")
    out = sysmon.check_server_update()
    assert out["latest"] == "0.2.0-beta.2" and out["prerelease"] is True and out["available"] and out["notes"] == "beta two" and out["channel"] == "beta"
    n = admin.get("/api/notifications").json()["items"][0]
    assert n["title"] == "Beta available: 0.2.0-beta.2"
    assert admin.get("/api/notifications", headers={"Accept-Language": "de"}).json()["items"][0]["title"] == "Beta verfügbar: 0.2.0-beta.2"


def test_a_stable_release_beats_its_own_betas(admin, repo, monkeypatch):
    monkeypatch.setattr(main, "VERSION_INFO", {"version": "0.2.0-beta.2", "commit": ""})
    sysmon.set_channel("beta")
    sysmon.set_transport(github(releases=[{"tag_name": "v0.2.0", "prerelease": False, "draft": False, "body": "", "html_url": "https://x"}] + RELEASES))
    assert sysmon.check_server_update()["latest"] == "0.2.0" and sysmon.check_server_update()["available"]
    sysmon.set_transport(github(releases=RELEASES))  # nothing newer than the beta that is installed
    assert sysmon.check_server_update()["available"] is False and system_keys() == []


def test_tags_without_a_release_follow_the_channel(admin, repo):
    tags = [{"name": "v0.3.0"}, {"name": "v0.4.0-beta.1"}, {"name": "v0.2.0"}]
    sysmon.set_transport(github(release=None, tags=tags))
    assert sysmon.check_server_update()["latest"] == "0.3.0"  # stable: a tag with a pre-release suffix does not count
    sysmon.set_channel("beta")
    sysmon.set_transport(github(releases=[], tags=tags))
    out = sysmon.check_server_update()
    assert out["latest"] == "0.4.0-beta.1" and out["prerelease"] is True


def test_the_admin_switches_the_channel_and_the_check_runs_again(admin, bob, repo):
    sysmon.set_transport(github({"tag_name": "v9.0.0", "body": "", "html_url": "https://x", "prerelease": False}, releases=[{"tag_name": "v9.1.0-beta.1", "prerelease": True, "draft": False, "body": "b", "html_url": "https://x/b"}]))
    assert bob.put("/api/admin/updates/channel", json={"channel": "beta"}).status_code == 403
    assert admin.put("/api/admin/updates/channel", json={"channel": "nightly"}).status_code == 422
    r = admin.put("/api/admin/updates/channel", json={"channel": "beta"}).json()
    assert r["server"]["channel"] == "beta" and r["server"]["latest"] == "9.1.0-beta.1" and r["channels"] == ["stable", "beta"]
    assert admin.get("/api/admin/updates").json()["server"]["channel"] == "beta"
    r = admin.put("/api/admin/updates/channel", json={"channel": "stable"}).json()  # back: the beta notice goes, the stable one stays
    assert r["server"]["channel"] == "stable" and r["server"]["latest"] == "9.0.0" and r["server"]["prerelease"] is False and system_keys() == ["server_update"]
    assert admin.get("/api/notifications").json()["items"][0]["title"] == "Server update available: 9.0.0"


def test_the_environment_sets_the_default_channel_and_garbage_means_stable(admin, monkeypatch):
    monkeypatch.setattr(sysmon, "UPDATE_CHANNEL", "beta")
    assert sysmon.channel() == "beta"
    sysmon.set_channel("stable")  # the admin's choice wins
    assert sysmon.channel() == "stable"
    db.set_setting(sysmon.CHANNEL_SETTING, "nightly")
    assert sysmon.channel() == "beta"  # an unknown value is ignored: the environment's default applies
    monkeypatch.setattr(sysmon, "UPDATE_CHANNEL", "bogus")
    assert sysmon.channel() == "stable"
