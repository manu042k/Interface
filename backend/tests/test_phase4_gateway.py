"""Phase 4 — ST-021: Goal Gateway."""

from __future__ import annotations

import asyncio

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cua.api.gateway import create_app


@pytest_asyncio.fixture
async def client(offline_config, mockbank):
    app = create_app(offline_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # trigger startup
        await c.get("/healthz")
        c._mockbank = mockbank  # type: ignore[attr-defined]
        yield c
    # shutdown
    sys = app.state.system
    if sys:
        await sys.shutdown()


async def test_start_run_returns_run_id_immediately(client):
    r = await client.post("/runs", json={
        "goal": "look up member 12345 and read their current savings balance",
        "target": f"{client._mockbank}/search",
    })
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"] and body["status"] == "pending"

    # poll to completion
    run_id = body["run_id"]
    for _ in range(100):
        g = await client.get(f"/runs/{run_id}")
        if g.json()["status"] in {"completed", "failed", "stuck", "dead_end"}:
            break
        await asyncio.sleep(0.1)
    assert g.json()["status"] == "completed", g.json()


async def test_offlist_target_rejected_no_run_created(client):
    r = await client.post("/runs", json={
        "goal": "x",
        "target": "http://evil.example/steal",
    })
    assert r.status_code == 422
    assert "allowlist" in r.text.lower()


async def test_unknown_run_404(client):
    r = await client.get("/runs/nope")
    assert r.status_code == 404
