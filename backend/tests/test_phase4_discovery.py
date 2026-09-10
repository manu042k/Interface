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


async def test_repeated_failing_action_escalates_instead_of_grinding_to_max_steps(system, mockbank):
    """A tool that keeps FAILING (e.g. a malformed assert_state condition) used to
    sail past the no-progress guard, which only counts successful repeats, and
    burn every step. It must now bail after a short failure streak."""
    from cua.surface.base import ActionResult

    real = system.adapter.execute

    async def flaky(session, action):
        if action.type.value == "assert_state":
            return ActionResult(ok=False, error="condition did not evaluate")
        return await real(session, action)

    system.adapter.execute = flaky  # type: ignore[assignment]

    # a goal that pushes the agent toward assert_state quickly
    run, _ = await system.orchestrator.run_discovery(
        goal="open the search page and immediately verify it loaded",
        target=f"{mockbank}/search",
    )
    assert run.status in {RunStatus.STUCK, RunStatus.DEAD_END}
    assert run.step_count < 20  # bailed on the streak, did not grind to the ceiling


def test_salvage_condition_rebuilds_a_degenerate_assert_state():
    """A model that sends `condition: {}` and puts the phrase in (mis-keyed)
    reasoning should still get a usable text_present condition."""
    from cua.discovery.orchestrator import _salvage_condition as sc

    # Gemini's real failure: empty condition + typo'd reasoning key + quoted phrase
    assert sc({"condition": {}, "reas1oning": 'check for the "CHANGES SAVED" text'}) == {
        "kind": "text_present", "params": {"any": ["CHANGES SAVED"]}
    }
    # ALL-CAPS run, no quotes
    assert sc({"condition": {}, "reasoning": "verify MEMBER INFORMATION UPDATED shows"}) == {
        "kind": "text_present", "params": {"any": ["MEMBER INFORMATION UPDATED"]}
    }
    # phrase parked at the top level
    assert sc({"text": "TRANSFER POSTED"})["params"]["any"] == ["TRANSFER POSTED"]
    # a url hint
    assert sc({"pattern": "/members/\\d+"}) == {
        "kind": "url_matches", "params": {"pattern": "/members/\\d+"}
    }
    # already well-formed -> untouched
    good = {"kind": "text_present", "params": {"any": ["x"]}}
    assert sc({"condition": good}) == good
    # nothing salvageable -> empty (caller fails once with a sharp hint)
    assert sc({"condition": {}, "reasoning": "the page should be loaded now"}) == {}


def test_pop_reasoning_tolerates_a_mangled_key():
    from cua.llm.providers import _pop_reasoning

    a = {"reas1oning": "because", "target": {"name": "q"}}
    assert _pop_reasoning(a) == "because"
    assert "reas1oning" not in a  # popped, so it doesn't pollute the tool args
    assert _pop_reasoning({"reasoning": "clean"}) == "clean"
    assert _pop_reasoning({"target": {}}) == ""


def test_salvage_from_state_lifts_a_confirmation_phrase_off_the_screen():
    """When the model gives assert_state nothing usable AND no phrase in its
    args, but the screen it just observed shows a confirmation, assert THAT."""
    from cua.discovery.orchestrator import _salvage_from_state
    from cua.models import SurfaceState

    won = SurfaceState(
        url="http://x/", title="MEMBER INFORMATION UPDATED", ax_summary="",
        dom_excerpt="VISIBLE PAGE TEXT:\nCHANGES SAVED\nMember 100987 contact information has been updated.",
        fingerprint="a",
    )
    cond = _salvage_from_state(won)
    assert cond and cond["kind"] == "text_present"
    assert "CHANGES SAVED" in cond["params"]["any"]

    # a plain form / non-result screen -> nothing to salvage (fail once, hint)
    plain = SurfaceState(
        url="http://x/", title="Update Member", ax_summary="",
        dom_excerpt="VISIBLE PAGE TEXT:\nUPDATE MEMBER INFORMATION\nE-mail Phone Address",
        fingerprint="b",
    )
    assert _salvage_from_state(plain) is None
    assert _salvage_from_state(None) is None
