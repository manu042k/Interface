"""Generate a REAL discovery -> stuck -> live-session handoff -> resume
evidence bundle against the live, external ParaBank site (parabank.parasoft.com)
- the assignment brief's own example of "a public proxy target" - using the
actual EscalationService/OperatorConsole code paths, not a mock. Same shape
and mechanism as scripts/gen_handoff_evidence.py, which uses local MockBank;
this proves the mechanism isn't MockBank-specific.

Run from backend/ with the venv active. No local target process needed -
ParaBank is a real, publicly reachable site.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

os.environ.setdefault("CUA_DB_PATH", ".data/evidence-handoff-parabank.db")
Path(".data/evidence-handoff-parabank.db").unlink(missing_ok=True)

from cua.assembly import build_system  # noqa: E402
from cua.cli import _dump_run_evidence  # noqa: E402
from cua.config import load_config  # noqa: E402

# john / demo is ParaBank's own published public demo login (parasoft.com's
# QA-training sandbox account, not a real customer) - the same credentials
# used by the parabank_transfer_gate_approve_v2 capability already in this
# repo's artifact store.
GOAL = (
    "Sign in with username john and password demo. Open a new account for "
    "this customer. The account type and which existing account to fund it "
    "from are business decisions only a supervisor is authorized to make - "
    "you must not choose them yourself. If you reach the new-account form "
    "and it is not already filled in with a supervisor's decision, do not "
    "select or submit anything on it; stop there and hand the decision to "
    "a human before doing anything else on that form. Once a supervisor has "
    "opened the account, verify the confirmation screen, extract the new "
    "account number, and report it. If you find the account already "
    "created when you next look, do not fill in or re-submit the form "
    "yourself - just observe the current screen, extract the new account "
    "number, and finish. If it is not yet visible, re-observe once more "
    "before concluding."
)
TARGET = "https://parabank.parasoft.com/parabank/index.htm"
TENANT = "parabank-demo"


async def main() -> None:
    cfg = load_config(strict=False)
    system = build_system(cfg)

    # Mirrors what the gateway's own POST /runs does for a typed target
    # (api/gateway.py::_validate_target_or_400): register this run's entry
    # host as permitted for this tenant. Egress to any OTHER host stays
    # blocked and the risky-route confirmation gate still applies - this
    # only opens the door to the site named in TARGET.
    system.policy.allow_target(TENANT, TARGET)

    task = asyncio.create_task(
        system.orchestrator.run_discovery(
            goal=GOAL, target=TARGET, tenant=TENANT,
            params={"username": "john", "password": "demo"},
            handoff_wait_s=240,  # real block-and-wait, like a live operator session
        )
    )

    # Poll for the run to reach STUCK and open an intervention - mirrors what
    # a dashboard would do, using the real store the gateway also reads. A
    # real external site + real LLM latency means this takes longer than the
    # MockBank version.
    iv = None
    deadline = time.time() + 200
    while time.time() < deadline:
        await asyncio.sleep(1)
        ivs = system.escalation.list_interventions(status="open")
        if ivs:
            iv = ivs[0]
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

    # A script stands in for the human operator here, but it is paced like
    # one: a read/decide pause after taking control, then a human-scale gap
    # before each action, rather than firing everything back-to-back.
    async def human_pause(low: float, high: float) -> None:
        await asyncio.sleep(random.uniform(low, high))

    await human_pause(2.5, 4.0)  # operator reads the blank form before acting

    # A human decides: open a SAVINGS account funded from john's first
    # existing account. ParaBank's <select name="type"> uses 0=CHECKING,
    # 1=SAVINGS. <select name="fromAccountId"> lists john's real, live
    # account numbers - not known ahead of time - so the operator reads
    # them off the actual page, the way a person looking at the dropdown
    # would, rather than a value baked into the script.
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "select", "target": {"name": "type"}, "value": "SAVINGS"},
    )
    await human_pause(1.5, 3.0)
    snap = await system.adapter.snapshot(handle["session_id"])
    opts = re.findall(
        r'<select[^>]*name=["\']fromAccountId["\'][^>]*>(.*?)</select>', snap.html, re.S,
    )
    from_account_value = "13344"  # fallback if the regex can't find the live options
    if opts:
        first_opt = re.search(r'<option[^>]*value=["\']([^"\']+)["\']', opts[0])
        if first_opt:
            from_account_value = first_opt.group(1)
    print(f"operator reads the live 'from account' dropdown -> picks {from_account_value!r}")
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "select", "target": {"name": "fromAccountId"}, "value": from_account_value},
    )
    await human_pause(1.5, 3.0)
    await system.console.perform(
        iv.intervention_id, operator,
        {"type": "click", "target": {"role": "button", "name": "Open New Account"}},
    )
    print("operator actions performed and recorded")

    # -- hand back: automation resumes on the SAME session --------------
    result = await system.console.release_control(iv.intervention_id, operator)
    print(f"handed back -> {result}")

    run_record, transcript = await task
    print(f"final run status -> {run_record.status} ({run_record.detail})")

    out_dir = Path("../evidence/07-discovery-handoff-parabank")
    summary = {
        "kind": "discovery-handoff",
        "run_id": run_record.run_id,
        "goal": GOAL,
        "target": TARGET,
        "vendor": "parabank (real, external parasoft.com demo site)",
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
