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
