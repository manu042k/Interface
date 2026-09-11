"""Generate a REAL discovery -> stuck -> live-session handoff -> resume
evidence bundle, matching the shape of evidence/01-05 (events.jsonl,
screenshots, summary.json), using the actual EscalationService/
OperatorConsole code paths - not a mock.

Run from backend/ with the venv active and MockBank up.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

os.environ.setdefault("CUA_DB_PATH", ".data/evidence-handoff.db")
Path(".data/evidence-handoff.db").unlink(missing_ok=True)

from cua.assembly import build_system  # noqa: E402
from cua.cli import _dump_run_evidence  # noqa: E402
from cua.config import load_config  # noqa: E402
from cua.models import RunStatus  # noqa: E402


GOAL = (
    "Search for member 12345 and open their record. A new sub-account needs "
    "to be opened for this member, but the account type and the deposit "
    "amount are business decisions only a supervisor is authorized to make - "
    "you must not choose them yourself. If you reach the new-sub-account "
    "form and it is not already filled in with a supervisor's decision, do "
    "not select, type, or submit anything on it; stop there and hand the "
    "decision to a human before doing anything else on that form. Once a "
    "supervisor has created the sub-account, verify the confirmation "
    "screen, extract the confirmation number, and report it. If you find "
    "the sub-account already created when you next look, do not fill in or "
    "re-submit the form yourself - just observe the current screen, extract "
    "the confirmation number, and finish. If the confirmation is not yet "
    "visible, re-observe once more before concluding."
)
TARGET = "http://127.0.0.1:8799/search"


async def main() -> None:
    cfg = load_config(strict=False)
    system = build_system(cfg)

    task = asyncio.create_task(
        system.orchestrator.run_discovery(
            goal=GOAL, target=TARGET, tenant="default",
            params={"member_id": "12345"},
            handoff_wait_s=120,  # real block-and-wait, like a live operator session
        )
    )

    # Poll for the run to reach STUCK and open an intervention - mirrors what
    # a dashboard would do, using the real store the gateway also reads.
    run = None
    iv = None
    deadline = time.time() + 90
    while time.time() < deadline:
        await asyncio.sleep(1)
        ivs = system.escalation.list_interventions(status="open")
        if ivs:
            iv = ivs[0]
            run = iv  # just need run_id off it below
            break
    if iv is None:
        print("No intervention opened within the wait window - goal likely completed unaided.")
        run_record, transcript = await task
        print(f"run -> {run_record.status} ({run_record.detail})")
        await system.shutdown()
        return

    print(f"STUCK -> intervention {iv.intervention_id} kind={iv.kind!r} reason={iv.reason!r}")
    if iv.kind != "handoff":
        print(f"got a {iv.kind} gate, not a handoff - take_control would (correctly) refuse it. "
              "Reword GOAL so the model calls stuck() before attempting the risky click.")
        system.console.decide(iv.intervention_id, approved=False, operator="demo-reviewer",
                               note="wrong intervention kind for this script - aborting")
        task.cancel()
        await system.shutdown()
        return

    # -- operator takes over the SAME live session ---------------------
    operator = "demo-reviewer"
    system.console.claim(iv.intervention_id, operator)
    handle = system.console.take_control(iv.intervention_id, operator)
    print(f"took control of session {handle['session_id']}")

    # A script stands in for the human operator here, but it is paced like one:
    # a read/decide pause after taking control, then a human-scale gap before
    # each action (reading the field, deciding the value, moving the pointer)
    # rather than firing all four back-to-back. These are real numbers picked
    # to be plausible, not a recording of an actual person.
    async def human_pause(low: float, high: float) -> None:
        await asyncio.sleep(random.uniform(low, high))

    await human_pause(2.5, 4.0)  # operator reads the blank form before acting

    # A human decides: open a "Money Market" sub-account with a $10 deposit.
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "select", "target": {"name": "acct_type"}, "value": "Money Market"},
    )
    await human_pause(1.5, 3.0)
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "type", "target": {"name": "amt"}, "value": "10.00"},
    )
    await human_pause(1.0, 2.5)
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "click", "target": {"role": "button", "name": "Review"}},
    )
    # MockBank's create route always shows a "Confirm sub-account creation"
    # interstitial on the first submit - a real second, deliberate confirm.
    await human_pause(1.5, 3.0)
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "click", "target": {"role": "button", "name": "Confirm creation"}},
    )
    print("operator actions performed and recorded")

    # -- hand back: automation resumes on the SAME session --------------
    result = await system.console.release_control(iv.intervention_id, operator)
    print(f"handed back -> {result}")

    run_record, transcript = await task
    print(f"final run status -> {run_record.status} ({run_record.detail})")

    out_dir = Path("../evidence/06-discovery-handoff")
    summary = {
        "kind": "discovery-handoff",
        "run_id": run_record.run_id,
        "goal": GOAL,
        "target": TARGET,
        "status": run_record.status,
        "detail": run_record.detail,
        "step_count": run_record.step_count,
        "intervention_id": transcript.intervention_id,
        "human_actions": system.escalation.get(iv.intervention_id).human_actions_log,
        "transcript": [
            {
                "step": e.step, "tool": e.tool_call.tool, "reasoning": e.tool_call.reasoning,
                "guardrail": e.guardrail_verdict, "ok": e.action_ok, "result": e.action_result,
            }
            for e in transcript.entries
        ],
    }
    _dump_run_evidence(cfg.evidence_root, run_record.run_id, out_dir, summary)
    print(f"evidence -> {out_dir}")

    await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
