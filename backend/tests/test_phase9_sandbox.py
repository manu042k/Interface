"""Phase 9 — ST-041/042: per-session isolation seam."""

from __future__ import annotations

import pytest_asyncio

from cua.models import ActionType
from cua.surface.base import Action
from cua.surface.playwright_adapter import PlaywrightAdapter
from cua.surface.sandbox import SessionBudget, SessionWatchdog


@pytest_asyncio.fixture
async def adapter():
    a = PlaywrightAdapter(headed=False, session_wall_clock_s=0.05)
    yield a
    await a.shutdown()


# --- ST-041: egress restricted to allowlisted host ------------------
async def test_egress_blocked_and_reported(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    await adapter.execute(h, Action(type=ActionType.NAVIGATE, value="http://example.org/x"))
    assert any("example.org" in u for u in adapter.egress_report(h))


async def test_two_sessions_do_not_share_state(adapter, mockbank):
    h1 = await adapter.open_session(f"{mockbank}/member/12345")  # sets the sessnotice cookie
    await adapter.execute(h1, Action(type=ActionType.CLICK, target_description={"text": "Acknowledge and continue"}))
    h2 = await adapter.open_session(f"{mockbank}/member/12345")  # fresh context -> notice again
    snap = await adapter.snapshot(h2)
    assert "Session Notice" in snap.title  # cookie from h1 did not leak into h2


# --- ST-042: wall-clock watchdog ---------------------------------
async def test_watchdog_kills_runaway_session(adapter, mockbank):
    import asyncio

    h = await adapter.open_session(f"{mockbank}/search")
    await asyncio.sleep(0.12)  # exceed the 0.05s budget
    killed = await adapter.sweep_watchdog()
    assert h in killed
    # session is gone
    with_err = False
    try:
        await adapter.snapshot(h)
    except Exception:  # noqa: BLE001
        with_err = True
    assert with_err


def test_watchdog_no_false_positive_within_budget():
    wd = SessionWatchdog(SessionBudget(wall_clock_s=100))
    wd.register("s1")
    assert wd.sweep() == []
    assert wd.report()[0]["killed"] is False
