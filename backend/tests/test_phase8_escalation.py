"""Phase 8 — ST-036..ST-040: escalation & human handoff."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from cua.assembly import build_system
from cua.escalation.session_broker import Holder, LockError, SessionBroker
from cua.models import Condition, InterventionStatus, RunStatus


# --- ST-036: control lock -------------------------------------------
def test_lock_is_exclusive_and_cas():
    b = SessionBroker(default_ttl=60)
    a_lease = b.acquire("s1", Holder.AUTOMATION)
    assert b.holder("s1") == Holder.AUTOMATION
    with pytest.raises(LockError):
        b.acquire("s1", Holder.HUMAN)  # someone else holds it
    b.release(a_lease)
    b.acquire("s1", Holder.HUMAN)  # now free
    assert b.holder("s1") == Holder.HUMAN


def test_expired_lease_is_reaped_not_orphaned():
    b = SessionBroker(default_ttl=0.05)
    b.acquire("s2", Holder.AUTOMATION)
    time.sleep(0.12)
    assert b.holder("s2") == Holder.NONE  # lease lapsed
    reaped = b.reap_expired()
    assert "s2" in reaped
    b.acquire("s2", Holder.HUMAN)  # acquirable again after a crash


def test_release_requires_current_lease():
    b = SessionBroker()
    l1 = b.acquire("s3", Holder.AUTOMATION)
    b.release(l1)
    l2 = b.acquire("s3", Holder.HUMAN)
    with pytest.raises(LockError):
        b.release(l1)  # stale lease can't release the new holder's lock
    b.release(l2)


# --- full handoff flow ------------------------------------------
@pytest_asyncio.fixture
async def system(offline_config, mockbank):
    sys = build_system(offline_config)
    yield sys, mockbank
    await sys.shutdown()


async def test_stuck_discovery_opens_intervention_with_context(system):
    sys, mockbank = system
    run, transcript = await sys.orchestrator.run_discovery(
        goal="perform an unsupported back-office operation that has no screen",
        target=f"{mockbank}/search",
    )
    assert run.status == RunStatus.STUCK
    assert transcript.intervention_id is not None

    iv = sys.escalation.get(transcript.intervention_id)
    # ST-037: context bundle
    assert iv.reason
    # the operator is told WHAT the agent was going for, not just that it stopped
    assert iv.attempting and iv.attempting == iv.context["attempting"]
    assert iv.context["current_url"]
    assert iv.context["screenshot_ref"]  # a richer signal was captured
    assert iv.context["transcript_tail"]
    # ST-037: the automation session is HELD, not torn down
    assert sys.broker.holder(transcript.session_id) == Holder.AUTOMATION


async def test_operator_claims_takes_control_acts_and_hands_back(system):
    sys, mockbank = system
    run, transcript = await sys.orchestrator.run_discovery(
        goal="perform an unsupported back-office operation that has no screen",
        target=f"{mockbank}/search",
    )
    iv_id = transcript.intervention_id
    console = sys.console

    # visible in the operator inbox
    inbox = console.inbox("open")
    assert any(i["intervention_id"] == iv_id for i in inbox)

    # ST-038: claim
    console.claim(iv_id, operator="op_dana")
    assert sys.escalation.get(iv_id).status == InterventionStatus.CLAIMED

    # ST-039: take control of the SAME session (lock transfers to human)
    handle = console.take_control(iv_id, operator="op_dana")
    assert handle["session_id"] == transcript.session_id
    assert sys.broker.holder(transcript.session_id) == Holder.HUMAN

    # ST-039: human drives the live session; actions are recorded
    r1 = await console.perform(iv_id, "op_dana", {"type": "navigate", "value": f"{mockbank}/member/12345?ack=1"})
    assert r1["ok"]
    iv = sys.escalation.get(iv_id)
    assert [a["type"] for a in iv.human_actions_log] == ["navigate"]

    # ST-040: hand back; goal checkpoint now holds -> resolved as satisfied
    checkpoint = Condition(kind="text_present", params={"text": "Savings"})
    out = await console.release_control(iv_id, "op_dana", goal_checkpoint=checkpoint)
    assert out["resumed"] is True
    assert out["checkpoint_already_holds"] is True
    assert sys.escalation.get(iv_id).status == InterventionStatus.RESOLVED
    # control returned to automation
    assert sys.broker.holder(transcript.session_id) == Holder.AUTOMATION


async def test_risk_approval_gate_open_and_decide(system):
    """A risk_approval intervention: no takeover — Approve / Reject only."""
    sys, mockbank = system
    from cua.models import RunRecord

    run = RunRecord(run_id="r-risk", mode="discovery", goal="transfer 500",
                    tenant_id="default", app_target=f"{mockbank}/search")
    sess = await sys.adapter.open_session(f"{mockbank}/search", "default")
    sys.broker.register_session(sess, sess)

    iv = await sys.escalation.open_risk_approval(
        run=run, session_id=sess, step_index=7,
        proposed_action="click 'Transfer' (value: 500) — goal: transfer 500 from A to B",
        reason="committing an irreversible action on /transfer.htm",
    )
    assert iv.kind == "risk_approval"
    assert iv.proposed_action.startswith("click 'Transfer'")
    row = next(i for i in sys.console.inbox("open") if i["intervention_id"] == iv.intervention_id)
    assert row["kind"] == "risk_approval" and row["proposed_action"]

    # reject
    out = sys.console.decide(iv.intervention_id, approved=False, operator="op_kim", note="too large")
    assert out["decision"] == "rejected"
    assert sys.escalation.get(iv.intervention_id).status == InterventionStatus.RESOLVED
    # a resolved gate can't be decided again
    import pytest
    with pytest.raises(ValueError):
        sys.console.decide(iv.intervention_id, approved=True, operator="op_kim")

    await sys.adapter.close_session(sess)


async def test_two_risk_rejections_dead_end_instead_of_looping(system, monkeypatch):
    """A rejected risky action must not re-open the gate forever: the second
    rejection this run ends it DEAD_END."""
    sys, mockbank = system
    from cua.models import RiskClass
    from cua.policy.engine import PolicyDecision, PolicyVerdict

    real_check = sys.policy.check

    def force_confirm(ctx):
        d = real_check(ctx)
        if d.verdict == PolicyVerdict.ALLOW and str(ctx.action_type) == "click":
            return PolicyDecision(PolicyVerdict.REQUIRE_CONFIRMATION, "test: forced risky click", RiskClass.RISKY_IRREVERSIBLE)
        return d

    monkeypatch.setattr(sys.policy, "check", force_confirm)

    async def always_reject(*a, **k):
        return False

    monkeypatch.setattr(sys.orchestrator, "_await_risk_approval", always_reject)

    run, transcript = await sys.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        handoff_wait_s=30,
    )
    assert run.status == RunStatus.DEAD_END
    assert "rejected" in (run.detail or "").lower()
    # ended promptly, not ground to the step ceiling
    assert run.step_count < 12


def test_handback_note_arms_a_repeat_block(system):
    sys, _ = system
    from cua.discovery.orchestrator import DiscoveryTranscript, TranscriptEntry
    from cua.models import SurfaceState, ToolCall

    t = DiscoveryTranscript(run_id="r", goal="g", target="t", tenant="default")
    st = SurfaceState(url="u")
    for _ in range(3):
        c = ToolCall(tool="type", args={"target": {"name": "criteria.amount"}})
        t.entries.append(TranscriptEntry(9, st, c, None, False, {"ok": False}, "allow"))

    note = sys.orchestrator._handback_note(t, 9, "release_control")
    assert t.post_handoff_steps == 5
    assert any("criteria.amount" in s for s in t.post_handoff_block)
    assert "do NOT repeat" in note.lower() or "do not repeat" in note.lower()
    assert "criteria.amount" in note


async def test_repeated_handoff_for_same_blocker_dead_ends(system):
    """Bouncing back to a human for the SAME reason must end the run, not loop."""
    sys, mockbank = system
    from cua.models import RunRecord

    run = RunRecord(run_id="r-loop", mode="discovery", goal="g",
                    tenant_id="default", app_target=f"{mockbank}/search")
    sess = await sys.adapter.open_session(f"{mockbank}/search", "default")
    sys.broker.register_session(sess, sess)
    from cua.discovery.orchestrator import DiscoveryTranscript
    t = DiscoveryTranscript(run_id="r-loop", goal="g", target=f"{mockbank}/search", tenant="default")

    reason = "repeated type failed 4x in a row"
    # 1st handoff: opens an intervention, returns None (wait_s=0, non-interactive)
    out1 = await sys.orchestrator._escalate_and_wait(
        run, t, sess, 12, reason, "g", [], sys.orchestrator._logger_factory("r-loop"), 0.0,
    )
    assert out1 is None and t.handoff_count == 1
    assert run.status.value != "dead_end"

    # 2nd handoff, SAME reason -> capped, run ends dead_end, no new intervention wait
    out2 = await sys.orchestrator._escalate_and_wait(
        run, t, sess, 20, reason, "g", [], sys.orchestrator._logger_factory("r-loop"), 0.0,
    )
    assert out2 is None
    assert run.status.value == "dead_end"
    assert "escalated to a human" in (run.detail or "")

    await sys.adapter.close_session(sess)
