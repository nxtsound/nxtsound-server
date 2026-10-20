"""Starts the demo server on the demo data (default http://localhost:8030). Plugins can be installed from a local folder here."""
import os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
D = os.environ.get("DEMO_DIR", str(HERE / "work"))
os.environ.update(DB_PATH=f"{D}/data/server.db", COVER_DIR=f"{D}/data/covers", MUSIC_DIR=f"{D}/music", PLUGINS_DIR=f"{D}/plugins",
                  PLUGIN_DATA_DIR=f"{D}/data/plugin-data", ANALYZE="0", ENRICH="0", SYSMON_ON_START="0", PLUGIN_USE_VENV="0", LYRICS="0", SCAN_ON_START="0", DEFAULT_LANG="en")
os.makedirs(f"{D}/plugins", exist_ok=True)
os.chdir(HERE.parents[1]); sys.path.insert(0, os.getcwd())
import pluginhost; pluginhost.ALLOW_LOCAL_REPOS = True
import uvicorn; uvicorn.run("main:app", port=int(os.environ.get("DEMO_PORT", "8030")), log_level="warning")
