"""The example plugin (examples/hello-plugin) must speak the protocol completely and be installable by the server."""
import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import plugincheck
import pluginhost
import plugins

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "hello-plugin"


@pytest.fixture()
def hello_app(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUGIN_TOKEN", "secret")
    monkeypatch.setenv("PLUGIN_DATA_DIR", str(tmp_path / "data"))
    spec = importlib.util.spec_from_file_location("hello_app", EXAMPLE / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


def test_example_passes_protocol_check(hello_app):
    with TestClient(hello_app) as c:
        assert plugincheck.check("http://hello.test", "secret", "tone", transport=c._transport) == []


def test_example_is_a_valid_plugin_folder():
    spec = pluginhost.read_spec(EXAMPLE)
    assert spec["id"] == "hello" and spec["entry"] == "app:app"


def test_server_installs_and_runs_the_example(alice, monkeypatch, tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setattr(pluginhost, "PLUGINS_DIR", d)
    monkeypatch.setattr(pluginhost, "DATA_DIR", tmp_path / "plugin-data")
    pluginhost._state.clear()
    shutil.copytree(EXAMPLE, d / "hello", ignore=shutil.ignore_patterns("__pycache__", ".venv", "data"))
    try:
        assert pluginhost.discover() == ["hello"]
        pluginhost.start("hello")
        end = time.time() + 25
        while time.time() < end and pluginhost.status("hello")["state"] != "running":
            time.sleep(0.2)
        assert pluginhost.status("hello")["state"] == "running", pluginhost.status("hello")
        items = alice.get("/api/search", params={"q": "tone", "source": "plugins"}).json()["items"]
        assert [t["id"] for t in items] == ["hello:t1", "hello:t2", "hello:t3"] and items[0]["source_name"] == "Hello-Plugin"
        r = alice.get("/api/stream/hello:t1", headers={"Range": "bytes=0-43"})
        assert r.status_code == 206 and r.content[:4] == b"RIFF" and r.headers["content-type"] == "audio/wav"
        assert alice.get("/api/suggest", params={"q": "to", "source": "plugins"}).json()["suggestions"] == ["Tone A", "Tone C", "Tone E"]
    finally:
        pluginhost.shutdown()
        pluginhost.stop("hello")


def test_example_settings_work_and_pass_the_protocol_check(hello_app):
    from pluginproto import validate_manifest
    with TestClient(hello_app, headers={"Authorization": "Bearer secret"}) as c:
        m = validate_manifest(c.get("/manifest").json())
        assert m["name"] and m["description"] and m["version"] and [s["key"] for s in m["settings"]] == ["shout", "max_results"]
        assert len(c.get("/search", params={"q": "tone"}).json()["items"]) == 3
        assert c.put("/settings", json={"values": {"shout": True, "max_results": 2}}).status_code == 200
        items = c.get("/search", params={"q": "tone"}).json()["items"]
        assert [i["title"] for i in items] == ["TONE A", "TONE C"]
