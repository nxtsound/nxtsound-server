"""Plugin installation by ZIP upload (admin): layouts, safety checks, replace, switches, rights."""
import io
import shutil
import time
import zipfile
from pathlib import Path

import pytest

import main
import pluginhost
import plugins
from conftest import make_client
from test_pluginhost import FIXTURE, wait_state  # noqa: F401 (shared helpers)

H = {"Content-Type": "application/zip"}


@pytest.fixture()
def host(monkeypatch, tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setattr(pluginhost, "PLUGINS_DIR", d)
    monkeypatch.setattr(pluginhost, "DATA_DIR", tmp_path / "plugin-data")
    pluginhost._state.clear()
    yield d
    pluginhost.shutdown()
    for pid in list(pluginhost._state):
        pluginhost.stop(pid)


def make_zip(prefix: str = "", extra=None, plugin_json=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FIXTURE.iterdir():
            if f.is_file():
                data = plugin_json if (f.name == "plugin.json" and plugin_json is not None) else f.read_bytes()
                z.writestr(prefix + f.name, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def upload(client, data, **params):
    return client.post("/api/admin/plugins/upload", content=data, headers=H, params=params)


def test_upload_installs_and_runs_plugin_from_root_or_top_folder(alice, host):
    for prefix in ("", "demo-main/"):  # plain archive or GitHub-style "Download ZIP" with a top folder
        r = upload(alice, make_zip(prefix))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == "demo" and r.json()["source"] == "upload" and r.json()["kind"] == "managed"
        wait_state("demo")
        items = alice.get("/api/search", params={"q": "demo", "source": "plugins"}).json()["items"]
        assert items[0]["id"] == "demo:d1"
        assert alice.delete("/api/admin/plugins/demo").status_code == 200
        assert not (host / "demo").exists() and plugins.get("demo") is None  # uploaded plugins are removed completely


def test_upload_rejects_unsafe_and_broken_archives(alice, host):
    assert upload(alice, b"this is not a zip").status_code == 400
    for name in ("../evil.txt", "/abs.txt", "a/../../evil.txt", "a\\b.txt"):
        r = upload(alice, make_zip(extra={name: b"x"}))
        assert r.status_code == 400 and "unsafe" in r.json()["detail"], name
    link = zipfile.ZipInfo("demo/link")
    link.external_attr = 0o120777 << 16
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("plugin.json", (FIXTURE / "plugin.json").read_text())
        z.writestr(link, "/etc/passwd")
    r = upload(alice, buf.getvalue())
    assert r.status_code == 400 and "symbolic" in r.json()["detail"]
    assert upload(alice, make_zip(plugin_json='{"id": "demo"}')).status_code == 400  # invalid plugin.json
    only_text = io.BytesIO()
    with zipfile.ZipFile(only_text, "w") as z:
        z.writestr("readme.txt", "hello")
    r = upload(alice, only_text.getvalue())
    assert r.status_code == 400 and "plugin.json" in r.json()["detail"]
    assert list(host.iterdir()) == [] and plugins.list_plugins() == []  # nothing left behind


def test_upload_rejects_huge_archives(alice, host, monkeypatch):
    monkeypatch.setattr(pluginhost, "MAX_UNPACKED_BYTES", 100)
    r = upload(alice, make_zip())
    assert r.status_code == 400 and "too large" in r.json()["detail"]
    monkeypatch.setattr(pluginhost, "MAX_UNPACKED_BYTES", 300 << 20)
    monkeypatch.setattr(pluginhost, "MAX_ZIP_BYTES", 50)  # raw upload limit (also enforced by the security middleware)
    monkeypatch.setitem(main.UPLOAD_LIMITS, "/api/admin/plugins/upload", 50)
    assert upload(alice, make_zip()).status_code == 413


def test_duplicate_and_replace(alice, host):
    assert upload(alice, make_zip()).status_code == 200
    wait_state("demo")
    assert upload(alice, make_zip()).status_code == 400  # already exists
    new_json = '{"id": "demo", "name": "Demo 2", "version": "0.0.2", "description": "Demo", "api_version": 1, "entry": "app:app"}'
    r = upload(alice, make_zip(plugin_json=new_json), replace="demo")
    assert r.status_code == 200 and r.json()["version"] == "0.0.2" and r.json()["name"] == "Demo 2" and r.json()["source"] == "upload"
    wait_state("demo")
    assert upload(alice, make_zip(), replace="other").status_code == 400  # archive holds a different plugin
    assert alice.post("/api/admin/plugins/demo/update").status_code == 400  # update via upload only
    assert "upload" in alice.post("/api/admin/plugins/demo/update").json()["detail"]


def test_replace_only_for_uploaded_plugins(alice, host):
    shutil.copytree(FIXTURE, host / "demo", ignore=shutil.ignore_patterns("__pycache__"))
    pluginhost.discover()  # installed "by hand"
    r = upload(alice, make_zip(), replace="demo")
    assert r.status_code == 400 and "uploaded by ZIP" in r.json()["detail"].replace("installed by ZIP upload", "uploaded by ZIP")
    assert upload(alice, make_zip()).status_code == 400  # folder already there


def test_upload_switch_and_rights(alice, host, monkeypatch):
    bob = make_client("Bob")
    assert bob.post("/api/admin/plugins/upload", content=b"x", headers=H).status_code == 403
    assert alice.get("/api/admin/plugins").json()["upload_install"] is True
    monkeypatch.setattr(pluginhost, "UPLOAD_INSTALL", False)
    r = upload(alice, make_zip())
    assert r.status_code == 400 and "turned off" in r.json()["detail"]
    assert alice.get("/api/admin/plugins").json()["upload_install"] is False


def test_german_message_for_upload_errors(alice, host):
    alice.cookies.set("nx_lang", "de")
    r = upload(alice, b"nope")
    assert r.status_code == 400 and r.json()["detail"] == "Die Datei ist kein gültiges ZIP-Archiv."


def test_upload_as_multipart_form_data(alice, host):
    """Browsers send uploads as multipart/form-data (proxies and firewalls accept that, unlike raw ZIP bodies)."""
    r = alice.post("/api/admin/plugins/upload", files={"file": ("demo.zip", make_zip("demo-main/"), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "demo"
    wait_state("demo")
    assert alice.post("/api/admin/plugins/upload", files={"other": ("x.zip", b"x", "application/zip")}).json()["detail"] == "No file received"
    bad = alice.post("/api/admin/plugins/upload", files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert bad.status_code == 400 and "valid ZIP" in bad.json()["detail"]


def test_unwritable_plugins_folder_gives_a_clear_message(alice, host):
    import os
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    host.chmod(0o555)
    try:
        r = upload(alice, make_zip())
        assert r.status_code == 400 and "Cannot write to the plugins folder" in r.json()["detail"] and "user ID 1000" in r.json()["detail"]
        assert "Cannot write" in alice.get("/api/admin/plugins").json()["dir_problem"]
        assert alice.post("/api/admin/plugins/install", json={"repo": "https://example.com/x/y"}).json()["detail"].startswith("Cannot write")
    finally:
        host.chmod(0o755)
    assert alice.get("/api/admin/plugins").json()["dir_problem"] == ""


def test_unexpected_errors_answer_with_json(alice, host, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(pluginhost, "install_zip", boom)
    alice.app_state = None
    from fastapi.testclient import TestClient
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.update(alice.cookies)
    client.headers.update({"X-CSRF-Token": alice.csrf})
    r = client.post("/api/admin/plugins/upload", content=make_zip(), headers=H)
    assert r.status_code == 500 and r.json()["detail"].startswith("Unexpected error (reference ")
    assert "kaboom" not in r.text and "RuntimeError" not in r.text  # internal details stay in the log


def test_environments_inside_the_archive_are_ignored_not_rejected(alice, host):
    """A zipped project folder often contains its virtual environment (symbolic links, thousands of files); it is simply left out."""
    link = zipfile.ZipInfo("demo/.venv/bin/python")
    link.external_attr = 0o120777 << 16
    buf = io.BytesIO(make_zip("demo/"))
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr(link, "/usr/bin/python3")
        z.writestr("demo/.venv/lib/site-packages/big.py", b"x" * 1000)
        z.writestr("demo/.git/config", b"[core]")
        z.writestr("demo/__pycache__/app.cpython-312.pyc", b"\0")
    r = upload(alice, buf.getvalue())
    assert r.status_code == 200, r.text
    wait_state("demo")
    assert not (host / "demo" / ".git").exists() and not (host / "demo" / "__pycache__").exists()
    assert not (host / "demo" / ".venv" / "lib" / "site-packages" / "big.py").exists()  # nothing from the archive's environment arrived
