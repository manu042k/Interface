"""Phase 1 — ST-006..ST-009. Async Playwright against MockBank."""

from __future__ import annotations

import pytest
import pytest_asyncio

from cua.models import ActionType
from cua.surface.base import Action
from cua.surface.perception import Perception
from cua.surface.playwright_adapter import PlaywrightAdapter


@pytest_asyncio.fixture
async def adapter():
    a = PlaywrightAdapter(headed=False)
    yield a
    await a.shutdown()


# --- ST-006: session lifecycle ------------------------------------------
async def test_open_and_close_session(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search", tenant="default")
    assert h.startswith("sess_")
    snap = await adapter.snapshot(h)
    assert "Member Search" in snap.title or "CoreServ" in snap.title
    await adapter.close_session(h)
    with pytest.raises(Exception):
        await adapter.snapshot(h)


# --- ST-007: primitive actions ----------------------------------------
async def test_type_click_navigate_flow(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")

    r = await adapter.execute(h, Action(type=ActionType.TYPE, target_description={"role": "textbox"}, value="12345"))
    assert r.ok, r.error

    r = await adapter.execute(h, Action(type=ActionType.CLICK, target_description={"text": "Search"}))
    assert r.ok, r.error
    assert "/members" in r.url_after

    r = await adapter.execute(h, Action(type=ActionType.CLICK, target_description={"text": "Open"}))
    assert r.ok, r.error


async def test_wait_for_reports_timeout_not_exception(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    r = await adapter.execute(
        h,
        Action(
            type=ActionType.WAIT_FOR,
            condition={"kind": "text_present", "params": {"text": "this text never appears xyzzy"}},
            timeout_ms=800,
        ),
    )
    assert r.ok is False and r.timed_out is True and r.error is None


# --- ST-008: SurfaceState snapshot ----------------------------------
async def test_observe_returns_normalized_state(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    per = Perception()
    state = await per.observe(adapter, h)
    assert state.url.endswith("ack=1")
    assert state.fingerprint
    # balance text is in the DOM outline even though markup is hostile
    assert "Savings" in state.dom_excerpt
    assert state.ax_summary  # not empty (degrades to a message if truly bare)


async def test_fingerprint_stable_across_content_change(adapter, mockbank):
    per = Perception()
    h1 = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    s1 = await per.observe(adapter, h1)
    h2 = await adapter.open_session(f"{mockbank}/member/34567?ack=1")
    s2 = await per.observe(adapter, h2)
    # same page template, different member -> same structural fingerprint
    assert s1.fingerprint == s2.fingerprint


# --- ST-009: extract() ------------------------------------------------
async def test_extract_currency_shape(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    per = Perception()
    value, err = await per.extract(adapter, h, {"near": "Savings"}, "currency")
    assert err is None
    assert value["amount"] == 4182.55


async def test_extract_shape_mismatch_returns_structured_error(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    per = Perception()
    value, err = await per.extract(adapter, h, {"near": "Home Branch"}, "number")
    assert value is None
    assert err and "expected number" in err


# --- ST-041 seam: cross-host egress is blocked --------------------
async def test_cross_host_egress_blocked(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    sess = adapter._sess(h)
    r = await adapter.execute(h, Action(type=ActionType.NAVIGATE, value="http://example.com/"))
    # navigation to a non-allowlisted host is aborted at the route boundary
    assert r.ok is False or "example.com" not in r.url_after
    assert any("example.com" in u for u in sess.blocked_egress)
