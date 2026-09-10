"""Phase 4 — ST-018..ST-020: discovery loop end to end, offline."""

from __future__ import annotations

import pytest_asyncio

from cua.assembly import build_system
from cua.models import RunStatus


@pytest_asyncio.fixture
async def system(offline_config, mockbank):
    offline_config_dict = offline_config  # Config with scripted provider
    sys = build_system(offline_config_dict)
    yield sys
    await sys.shutdown()


async def test_discovery_completes_balance_goal_offline(system, mockbank):
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        tenant="default",
    )
    assert run.status == RunStatus.COMPLETED, run.detail
    # an extract step captured the currency value
    extracts = [e for e in transcript.entries if e.tool_call.tool == "extract"]
    assert extracts and extracts[0].action_result["extracted"]["amount"] == 4182.55
    # the interstitial was handled as part of discovery (a dismiss click happened)
    assert any("Acknowledge" in str(e.tool_call.args) for e in transcript.entries)


async def test_discovery_stops_at_max_steps(system, mockbank):
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        max_steps=2,
    )
    assert run.status == RunStatus.DEAD_END
    assert run.step_count <= 2


async def test_guardrail_block_is_fed_back(system, mockbank):
    # goal that would send the agent off-allowlist; offline pilot will get a
    # BLOCK note and then stuck rather than acting outside policy.
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        tenant="unknown-tenant",  # no allowlist -> every action blocked
    )
    assert run.status in {RunStatus.STUCK, RunStatus.DEAD_END}
    assert any(e.guardrail_verdict == "block" for e in transcript.entries)
    # a guardrail rejection can't be fixed by retrying: it force-escalates after
    # the 2nd block instead of burning every step
    assert run.step_count <= 6


async def test_all_providers_exhausted_pauses_then_stuck(offline_config, mockbank):
    from cua.llm.router import AllProvidersExhausted

    sys = build_system(offline_config)
    try:
        async def boom(*a, **k):
            raise AllProvidersExhausted("simulated")

        sys.agent.decide = boom  # type: ignore[assignment]
        run, transcript = await sys.orchestrator.run_discovery(
            goal="look up member 12345 and read their current savings balance",
            target=f"{mockbank}/search",
            exhausted_ceiling=2,
        )
        assert run.status == RunStatus.STUCK
        assert run.detail == "all_providers_exhausted"
    finally:
        await sys.shutdown()


def test_derived_login_success_check_only_for_pure_login_goals():
    """The heuristic that gives a bare 'log in' goal a finish line must NOT fire
    when login is just the first step of a larger task — that ended runs early
    (a goal like 'sign on and pull up member 100234 record' stopped at /menu)."""
    from cua.discovery.orchestrator import _derive_login_success_check as derive

    target = "https://legacy-core.example.com/signon"

    # pure login -> a success check IS derived
    for g in (
        "log in",
        "sign on to the legacy_core console",
        "log on to the system",
        "authenticate with the portal",
        "sign on as teller1",
    ):
        assert derive(g, target) is not None, g

    # login + a downstream task -> NO derived check (would truncate the run)
    for g in (
        "sign on and pull up member 100234 record",
        "login and read savings for member 12345",
        "sign on and look up member 100234 record",
        "sign on then open funds transfer",
        "log in and check the balance",
    ):
        assert derive(g, target) is None, g

    # not a login goal at all
    assert derive("read the savings balance for member 12345", target) is None
