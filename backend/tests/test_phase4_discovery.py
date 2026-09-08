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
