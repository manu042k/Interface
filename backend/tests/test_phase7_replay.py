"""Phase 7 — ST-030..ST-035: deterministic replay + the outcome taxonomy."""

from __future__ import annotations

import pytest_asyncio

from cua.assembly import build_system
from cua.models import ReplayOutcome, RunStatus


@pytest_asyncio.fixture
async def sys_with_capability(offline_config, mockbank, monkeypatch):
    """Discover -> record -> approve the read_savings_balance capability."""
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")  # clean discovery for a tidy artifact
    system = build_system(offline_config)
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        params={"member_id": "12345"},
    )
    assert run.status == RunStatus.COMPLETED, run.detail
    art = system.record(transcript, name="read_savings_balance", vendor_app_id="mockbank")
    system.store.promote(art.artifact_id, art.version, "approve", reviewer="alice")
    yield system, art, mockbank
    await system.shutdown()


# --- ST-031: happy-path deterministic replay -----------------------
async def test_replay_success_returns_outputs(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")
    result = await system.replay.execute(
        art, {"member_id": "12345"}, target=f"{mockbank}/search", run_id="inv1"
    )
    assert result.outcome == ReplayOutcome.SUCCESS, result.failure_detail
    assert result.outputs["savings_balance"]["amount"] == 4182.55
    assert result.steps_executed == len(art.steps)


# --- ST-032: business outcome, not a crash ------------------------
async def test_replay_member_not_found_is_business_outcome(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")
    result = await system.replay.execute(
        art, {"member_id": "00000"}, target=f"{mockbank}/search", run_id="inv2"
    )
    assert result.outcome == ReplayOutcome.BUSINESS_OUTCOME
    assert result.business_outcome_code == "member_not_found"
    assert result.failure_detail is None


async def test_replay_permission_denied_is_business_outcome(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")
    result = await system.replay.execute(
        art, {"member_id": "99999"}, target=f"{mockbank}/search", run_id="inv3"
    )
    assert result.outcome == ReplayOutcome.BUSINESS_OUTCOME
    assert result.business_outcome_code == "permission_denied"


# --- ST-033: recoverable condition, then success ------------------
async def test_replay_recovers_from_interstitial(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "1")  # the dialog fires during replay
    result = await system.replay.execute(
        art, {"member_id": "12345"}, target=f"{mockbank}/search", run_id="inv4"
    )
    assert result.outcome == ReplayOutcome.RECOVERABLE_THEN_SUCCESS
    assert "session_notice_interstitial" in result.recovered_conditions
    assert result.outputs["savings_balance"]["amount"] == 4182.55


# --- ST-034: hard failure with debuggable detail -----------------
async def test_replay_hard_failure_has_structured_detail(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")
    # Corrupt the final checkpoint so the run reaches the end but can't verify.
    broken = art.model_copy(deep=True)
    broken.checkpoint = broken.checkpoint.model_copy(update={"kind": "text_present", "params": {"text": "TOTALLY UNEXPECTED SCREEN xyzzy"}})
    result = await system.replay.execute(
        broken, {"member_id": "12345"}, target=f"{mockbank}/search", run_id="inv5"
    )
    assert result.outcome == ReplayOutcome.HARD_FAILURE
    fd = result.failure_detail
    assert fd is not None and fd.expected and fd.observed
    assert result.evidence_refs  # screenshot + dom captured


# --- ST-030: input validation at the boundary -------------------
async def test_replay_rejects_bad_params_before_touching_surface(sys_with_capability):
    system, art, mockbank = sys_with_capability
    errs = system.replay.validate_params(art, {})  # missing required member_id
    assert errs
    result = await system.replay.execute(art, {}, target=f"{mockbank}/search", run_id="inv6")
    assert result.outcome == ReplayOutcome.HARD_FAILURE
    assert result.failure_detail.step_index == -1  # never entered the step loop


# --- ST-035: non-idempotent step re-check (unit) ----------------
async def test_non_idempotent_recheck_prevents_double_submit(sys_with_capability, monkeypatch):
    system, art, mockbank = sys_with_capability
    # Build a tiny artifact with one non-idempotent click whose checkpoint already
    # holds -> executor must NOT retry it.
    from cua.models import ActionType, CapabilityArtifact, Condition, LocatorStrategy, Step

    calls = {"n": 0}
    real_execute = system.adapter.execute

    async def counting(session, action):
        if action.type == ActionType.CLICK:
            calls["n"] += 1
        return await real_execute(session, action)

    monkeypatch.setattr(system.adapter, "execute", counting)
    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")

    tiny = CapabilityArtifact(
        name="tiny", goal_description="click once", vendor_app_id="mockbank",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        steps=[Step(
            step_index=0, action_type=ActionType.CLICK, description="open record",
            locator_spec=[LocatorStrategy(kind="text", params={"text": "Open"}, rank=0, rationale="r")],
            step_checkpoint=Condition(kind="url_matches", params={"pattern": r"/member/\d+"}),
            idempotent=False,
        )],
        checkpoint=Condition(kind="url_matches", params={"pattern": r"/member/\d+"}),
    )
    result = await system.replay.execute(
        tiny, {}, target=f"{mockbank}/members?q=12345", run_id="inv7"
    )
    assert result.outcome in {ReplayOutcome.SUCCESS, ReplayOutcome.RECOVERABLE_THEN_SUCCESS}
    assert calls["n"] == 1  # clicked exactly once, no blind retry


# --- §3.6: a stuck replay escalates to a human, then resumes ----
async def test_stuck_replay_escalates_and_resumes_after_handback(sys_with_capability):
    """An unrecoverable step raises an intervention, holds the live session, and
    replay continues once an operator hands control back (brief §3.6)."""
    import asyncio

    from cua.models import FailureDetail, InterventionStatus, ReplayResult

    system, art, mockbank = sys_with_capability
    target_step = 1  # fail the 2nd recorded step exactly once

    real_run_step = system.replay._run_step
    fired = {"n": 0}

    async def flaky_run_step(artifact, step, *a, **kw):
        if step.step_index == target_step and fired["n"] == 0:
            fired["n"] = 1
            return ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                failure_detail=FailureDetail(
                    step_index=step.step_index, expected="the next screen", observed="stale screen"
                ),
            )
        return await real_run_step(artifact, step, *a, **kw)

    system.replay._run_step = flaky_run_step  # type: ignore[method-assign]

    task = asyncio.create_task(
        system.replay.execute(
            art, {"member_id": "12345"}, target=f"{mockbank}/search",
            run_id="inv_esc", handoff_wait_s=10.0,
        )
    )

    # wait for the intervention, then run the real claim -> take-control ->
    # release flow (the same calls the operator console makes)
    for _ in range(80):
        await asyncio.sleep(0.1)
        ivs = system.escalation.list_interventions()
        if ivs:
            break
    assert ivs, "replay never opened an intervention"
    iv = ivs[0]
    assert iv.run_id == "inv_esc"
    assert iv.status == InterventionStatus.OPEN
    assert iv.context["current_url"] and iv.context["session_id"]

    system.escalation.claim(iv.intervention_id, "op_test")
    system.escalation.take_control(iv.intervention_id, "op_test")
    await system.escalation.resume(iv.intervention_id, resolution="handed back by test")

    result = await asyncio.wait_for(task, timeout=15)
    assert result.outcome == ReplayOutcome.RECOVERABLE_THEN_SUCCESS
    assert "human_intervention" in result.recovered_conditions
    assert result.outputs["savings_balance"]["amount"] == 4182.55
    assert system.escalation.get(iv.intervention_id).status == InterventionStatus.RESOLVED
