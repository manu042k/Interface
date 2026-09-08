"""Shared fixtures: an offline config and a live MockBank server."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def mockbank() -> Iterator[str]:
    """Start MockBank in a background thread; yield its base URL."""
    from mockbank.app import app as flask_app

    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(
        target=lambda: flask_app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False),
        daemon=True,
    )
    server_thread.start()

    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz", timeout=0.5).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
    else:
        raise RuntimeError("MockBank did not start")

    yield base


@pytest.fixture
def offline_config(tmp_path, monkeypatch):
    """A Config wired to the scripted provider and a tmp data dir."""
    from cua.config import load_config

    monkeypatch.setenv("CUA_LLM_PROVIDERS", "scripted")
    monkeypatch.setenv("CUA_DB_PATH", str(tmp_path / "cua.db"))
    monkeypatch.setenv("CUA_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setenv("CUA_ALLOWLIST_PATH", os.path.join(os.path.dirname(__file__), "..", "config", "allowlist.example.json"))
    return load_config(strict=False)
