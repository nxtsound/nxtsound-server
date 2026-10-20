"""Logging: levels from environment and admin area, applied at once, recent messages for the admin area."""
import logging

import pytest

import logconf
from conftest import make_client


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    with logconf.db.db() as c:
        c.execute("DELETE FROM settings WHERE key = ?", (logconf.SETTING,))
    logconf.apply()


def level(name: str) -> int:
    return logging.getLogger(name).level


def test_defaults_and_environment(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    r = logconf.resolve()
    assert r["server"] == ("INFO", "default") and r["plugin_output"] == ("WARNING", "default") and r["other"] == ("WARNING", "default")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_LEVEL_LIBRARY", "ERROR")
    monkeypatch.setenv("LOG_ACCESS", "0")
    r = logconf.resolve()
    assert r["server"] == ("DEBUG", "env") and r["library"] == ("ERROR", "env") and r["access"] == ("OFF", "env")
    assert r["other"] == ("WARNING", "default")  # LOG_LEVEL does not touch third-party libraries
    monkeypatch.setenv("LOG_LEVEL", "nonsense")
    assert logconf.resolve()["server"] == ("INFO", "default")  # invalid values are ignored


def test_apply_sets_logger_levels(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logconf.apply()
    assert level("server") == logging.DEBUG and level("library") == logging.DEBUG and level("pluginhost") == logging.DEBUG
    monkeypatch.setenv("LOG_LEVEL_PLUGINS", "OFF")
    logconf.apply()
    assert level("plugins") > logging.CRITICAL and level("pluginhost") > logging.CRITICAL
    monkeypatch.setenv("LOG_ACCESS", "0")
    logconf.apply()
    assert level("uvicorn.access") > logging.CRITICAL


def test_admin_api_changes_levels_and_resets(alice):
    s = alice.get("/api/admin/logging").json()
    assert s["levels"] == ["OFF", "ERROR", "WARNING", "INFO", "DEBUG"] and [c["id"] for c in s["categories"]][:3] == ["server", "access", "library"]
    r = alice.put("/api/admin/logging", json={"levels": {"library": "DEBUG", "access": "OFF"}}).json()
    cats = {c["id"]: c for c in r["categories"]}
    assert cats["library"]["level"] == "DEBUG" and cats["library"]["source"] == "admin" and cats["access"]["level"] == "OFF"
    assert level("library") == logging.DEBUG and level("uvicorn.access") > logging.CRITICAL  # applied without a restart
    r = alice.put("/api/admin/logging", json={"levels": {"library": ""}}).json()
    assert {c["id"]: c for c in r["categories"]}["library"]["source"] != "admin" and level("library") == logging.INFO
    assert alice.put("/api/admin/logging", json={"levels": {"nope": "INFO"}}).status_code == 400
    assert alice.put("/api/admin/logging", json={"levels": {"server": "LOUD"}}).status_code == 400


def test_labels_are_translated(alice):
    alice.cookies.set("nx_lang", "de")
    labels = {c["id"]: c["label"] for c in alice.get("/api/admin/logging").json()["categories"]}
    assert labels["access"] == "Zugriffsprotokoll (HTTP-Anfragen)" and labels["library"] == "Bibliotheks-Scan"


def test_recent_messages_follow_the_levels(alice):
    alice.put("/api/admin/logging", json={"levels": {"server": "DEBUG"}})
    logging.getLogger("server").debug("a debug message for the test")
    logging.getLogger("server").info("an info message for the test")
    lines = alice.get("/api/admin/logs", params={"limit": 50}).json()["lines"]
    texts = [x["message"] for x in lines]
    assert "a debug message for the test" in texts and "an info message for the test" in texts
    alice.put("/api/admin/logging", json={"levels": {"server": "ERROR"}})
    logging.getLogger("server").info("hidden info message")
    logging.getLogger("server").error("visible error message")
    texts = [x["message"] for x in alice.get("/api/admin/logs", params={"limit": 50}).json()["lines"]]
    assert "hidden info message" not in texts and "visible error message" in texts
    entry = next(x for x in lines if x["message"] == "an info message for the test")
    assert entry["level"] == "INFO" and entry["logger"] == "server" and len(entry["time"]) == 19


def test_logging_is_admin_only(alice):
    bob = make_client("Bob")
    assert bob.get("/api/admin/logging").status_code == 403
    assert bob.put("/api/admin/logging", json={"levels": {}}).status_code == 403
    assert bob.get("/api/admin/logs").status_code == 403


def test_scan_logs_summary(lib):
    import library
    logging.getLogger("library").setLevel(logging.INFO)
    library.scan(full=True)
    assert any("Scan finished" in x["message"] for x in logconf.recent(100))
