"""Phase 6 — ST-027..ST-029."""

from __future__ import annotations

import pytest_asyncio

from cua.models import LocatorStrategy
from cua.replay.locator import LocatorResolutionEngine
from cua.surface.playwright_adapter import PlaywrightAdapter


@pytest_asyncio.fixture
async def adapter():
    a = PlaywrightAdapter(headed=False)
    yield a
    await a.shutdown()


async def test_first_strategy_matches_no_drift(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "Search"}, rank=0, rationale="r"),
        LocatorStrategy(kind="text", params={"text": "Search"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp1", step_index=0)
    assert res.ok and res.matched_rank == 0 and res.drift_signal is False


async def test_falls_back_and_flags_drift(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "does-not-exist"}, rank=0, rationale="r"),
        LocatorStrategy(kind="relative_to_landmark", params={"near": "Savings"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp2", step_index=3)
    assert res.ok and res.matched_rank == 1 and res.drift_signal is True
    assert res.tried[0]["matched"] is False


async def test_unresolvable_returns_structured_error(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "nope"}, rank=0, rationale="r"),
        LocatorStrategy(kind="text", params={"text": "also nope zzz"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp3", step_index=0)
    assert res.ok is False
    assert "unresolvable target" in res.error
    assert len(res.tried) == 2 and all(not t["matched"] for t in res.tried)


async def test_cache_singleflight(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    calls = {"n": 0}
    real = adapter.try_strategy

    async def counting(*a, **k):
        calls["n"] += 1
        return await real(*a, **k)

    adapter.try_strategy = counting  # type: ignore[assignment]
    eng = LocatorResolutionEngine()
    spec = [LocatorStrategy(kind="role_name", params={"role": "button", "name": "Search"}, rank=0, rationale="r")]

    import asyncio

    results = await asyncio.gather(*[
        eng.resolve(spec, adapter, h, artifact_version=2, surface_fingerprint="fpX", step_index=0)
        for _ in range(5)
    ])
    assert all(r.ok for r in results)
    assert calls["n"] == 1  # coalesced: one real resolution for five concurrent callers

    dropped = eng.invalidate(fingerprint="fpX")
    assert dropped == 1
