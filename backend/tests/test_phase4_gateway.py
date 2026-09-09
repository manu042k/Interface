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


async def test_typed_target_host_is_auto_allowed(offline_config, mockbank):
    # The site the operator names is the site they want tested — validation
    # registers its host and accepts it, without an allowlist file entry.
    # Egress to any *other* host is still blocked mid-run (see
    # test_phase2_policy::test_allow_target_opens_the_typed_host_only).
    import pytest
    from fastapi import HTTPException

    from cua.api.gateway import _validate_target_or_400
    from cua.assembly import build_system
    from cua.policy.engine import ActionContext

    sys = build_system(offline_config)
    try:
        out = _validate_target_or_400(sys, "default", "https://legacy-console.example/signon")
        assert out.startswith("https://legacy-console.example/")
        assert sys.policy.check(
            ActionContext("default", "navigate", "https://legacy-console.example/menu")
        ).allowed
        with pytest.raises(HTTPException) as ei:
            _validate_target_or_400(sys, "default", "not-a-url")
        assert ei.value.status_code == 422
    finally:
        await sys.shutdown()


async def test_unknown_run_404(client):
    r = await client.get("/runs/nope")
    assert r.status_code == 404
