"""ST-019/020: the discovery Orchestrator.

State machine for one discovery run: observe -> decide -> guardrail-check -> act,
until done / stuck / max-steps / timeout. Guardrail rejections are fed back to
the agent as an observation (not silently dropped). `stuck` and
`all_providers_exhausted` both route to a pause-and-hold path — the session is
kept open for Phase 8 escalation, never torn down.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..events import RunLogger
from ..llm.router import AllProvidersExhausted, AllProvidersOutOfBalance, LLMRouter
from ..models import (
    ActionType,
    RiskClass,
    RunMode,
    RunRecord,
    RunStatus,
    SurfaceState,
    ToolCall,
)
from ..policy.engine import ActionContext, PolicyEngine, PolicyVerdict
from ..surface.base import Action, SurfaceAdapter
from ..surface.perception import Perception
from .agent import DiscoveryAgent

_TOOL_TO_ACTION = {
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "select": ActionType.SELECT,
    "navigate": ActionType.NAVIGATE,
    "wait_for": ActionType.WAIT_FOR,
    "extract": ActionType.EXTRACT,
    "assert_state": ActionType.ASSERT_STATE,
    "scroll": ActionType.SCROLL,
    "press_key": ActionType.PRESS_KEY,
}


@dataclass
class TranscriptEntry:
    step: int
    state_before: SurfaceState
    tool_call: ToolCall
    action: Action | None
    action_ok: bool
    action_result: dict[str, Any]
    guardrail_verdict: str
    extract_as: str | None = None


@dataclass
class DiscoveryTranscript:
    run_id: str
    goal: str
    target: str
    tenant: str
    params: dict[str, Any] = field(default_factory=dict)
    entries: list[TranscriptEntry] = field(default_factory=list)
    done_outputs: dict[str, Any] = field(default_factory=dict)
    final_state: SurfaceState | None = None
    stuck_reason: str | None = None
    # set when the run ended on a recognised business-outcome screen:
    # (code, message, the exact phrases that matched)
    business_outcome: tuple[str, str, list[str]] | None = None
    session_id: str | None = None
    intervention_id: str | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        config: Config,
        adapter: SurfaceAdapter,
        perception: Perception,
        agent: DiscoveryAgent,
        policy: PolicyEngine,
        router: LLMRouter,
        logger_factory: Any,
        escalation: Any | None = None,
        broker: Any | None = None,
        sandbox_manager: Any | None = None,
    ) -> None:
        self.cfg = config
        self.adapter = adapter
        self.perception = perception
        self.agent = agent
        self.policy = policy
        self.router = router
        self.sandbox_manager = sandbox_manager
        self._logger_factory = logger_factory  # (run_id) -> RunLogger
        self.escalation = escalation
        self.broker = broker

    async def run_discovery(
        self,
        *,
        goal: str,
        target: str,
        tenant: str = "default",
        params: dict[str, Any] | None = None,
        run: RunRecord | None = None,
        confirm_risky: bool = False,
        exhausted_ceiling: int = 20,
        max_steps: int | None = None,
        success_check: dict[str, Any] | None = None,
        # >0 makes a stuck discovery BLOCK for a human hand-back and then resume.
        # 0 (default, used by tests) opens the intervention and returns STUCK.
        handoff_wait_s: float = 0.0,
    ) -> tuple[RunRecord, DiscoveryTranscript]:
        params = params or {}
        step_budget = max_steps if max_steps is not None else self.cfg.max_steps
        run = run or RunRecord(mode=RunMode.DISCOVERY, tenant_id=tenant, app_target=target, goal=goal)
        run.status = RunStatus.RUNNING
        log: RunLogger = self._logger_factory(run.run_id)
        log.run_started("discovery", target, goal)

        transcript = DiscoveryTranscript(run_id=run.run_id, goal=goal, target=target, tenant=tenant, params=params)
        self._run_target = target  # for _discovery_business_outcome's per-host library lookup

        # A goal that is ONLY "log in" has no explicit finish line, so the model
        # can sign on successfully and then thrash (re-click submit, sign off,
        # retry...). Derive one: done once you've LEFT the auth path with no
        # rejection text. But NOT when login is just the first step of a larger
        # task ("log in AND read the balance") — that would end the run early.
        _is_login = bool(re.search(r"\b(log\s?in|log\s?on|sign\s?on|sign\s?in|authenticat)", goal, re.I))
        _has_downstream = bool(re.search(
            r"\b(read|extract|check|view|get|fetch|look ?up|find|search|open|create|add|"
            r"transfer|update|change|edit|place|submit|post|deposit|withdraw|balance|"
            r"amount|then|after (that|logging|signing)|navigate|go to)\b",
            goal, re.I,
        ))
        if success_check is None and _is_login and not _has_downstream:
            from urllib.parse import urlparse

            auth_seg = (urlparse(target).path.rsplit("/", 1)[-1] or "signon").lower()
            success_check = {
                "kind": "all_of",
                "params": {"conditions": [
                    {"kind": "url_matches", "params": {
                        "pattern": rf"^(?!.*/(?:{re.escape(auth_seg)}|signon|login|sign-?in|auth)\b).+"
                    }},
                    {"kind": "text_absent", "params": {"any": [
                        "invalid operator", "invalid credentials", "incorrect password",
                        "login failed", "sign-on failed", "not authorized", "try again",
                        "access denied",
                    ]}},
                ]},
            }
            log.event(0, "derived_success_check", detail="login goal — done once off the auth page with no error")

        deadline = time.time() + self.cfg.run_timeout_seconds
        history: list[str] = []
        note: str | None = None
        last_call: ToolCall | None = None  # for "what the agent was attempting" on escalation
        last_ok_tool: str | None = None  # last action that actually ran, for the done gate
        last_ok_step = -99  # step index of that last successful action
        done_nudged = 0  # rejected `done` calls (no successful verify before them)
        stuck_nudged = 0  # `stuck` calls pushed back because progress was just made
        policy_blocks = 0  # consecutive guardrail rejections the model can't fix by retrying
        used_params: set[str] = set()  # params whose value was actually typed/selected
        unused_param_nudged = 0

        from ..sandbox.session import close_run_surface, open_run_surface

        surface = await open_run_surface(
            adapter=self.adapter, sandbox_manager=self.sandbox_manager,
            target=target, tenant=tenant, run=run, logger=log,
        )
        session = surface.session_handle
        # no-progress loop guard: (tool, target) signature + a hash of the screen
        last_sig: str | None = None
        repeats = 0
        # cyclic-thrash guard: how many times each (tool|target) ran this run,
        # even when broken up by other actions (type A, type B, type A, ...).
        sig_counts: dict[str, int] = {}
        cycle_nudged = False
        try:
            step = 0
            while True:
                run.step_count = step  # keep the live view current
                if step >= step_budget:
                    run.status = RunStatus.DEAD_END
                    run.detail = f"max steps ({step_budget}) reached without done/stuck"
                    log.event(step, "dead_end", reason=run.detail)
                    break
                if time.time() > deadline:
                    run.status = RunStatus.DEAD_END
                    run.detail = "run timeout"
                    log.event(step, "dead_end", reason=run.detail)
                    break

                state = await self.perception.observe(
                    self.adapter, session, sink=log.sink, run_id=run.run_id, step=step
                )

                try:
                    call = await self._decide_with_backoff(
                        goal, state, history, step, step_budget, params, note, exhausted_ceiling, log
                    )
                except AllProvidersOutOfBalance as exc:
                    run.status = RunStatus.FAILED
                    run.detail = "llm providers out of credits — run stopped"
                    transcript.stuck_reason = run.detail
                    log.event(step, "run_stopped", reason="providers_out_of_balance", detail=str(exc))
                    log.run_finished("failed", detail=run.detail)
                    break
                note = None
                if call is None:
                    run.status = RunStatus.STUCK
                    run.detail = "all_providers_exhausted"
                    transcript.stuck_reason = run.detail
                    log.stuck(step, run.detail)
                    break

                log.decision(step, call.tool, call.args, call.reasoning)
                if call.tool not in ("observe", "stuck", "done"):
                    last_call = call

                if call.usage:
                    run.llm_calls += 1
                    run.tokens_in += int(call.usage.get("prompt_tokens", 0) or 0)
                    run.tokens_out += int(call.usage.get("completion_tokens", 0) or 0)
                    log.event(
                        step, "tokens", calls=run.llm_calls,
                        tokens_in=run.tokens_in, tokens_out=run.tokens_out,
                    )

                if call.tool == "observe":
                    history.append("observe -> re-read screen")
                    continue

                if call.tool == "done":
                    # A `done` must be earned by a preceding verification that
                    # SUCCEEDED - the assert_state/extract that ran just before is
                    # what the recorder keeps as the replay checkpoint. This is
                    # checked EVERY time, not once: a model that fabricates an
                    # answer, gets nudged, fails an assert, then re-issues `done`
                    # must not slip a bogus capability through.
                    if last_ok_tool not in ("assert_state", "extract"):
                        done_nudged += 1
                        history.append("done -> REJECTED: no successful verify precedes it")
                        if done_nudged >= 3:
                            reason = (
                                "the agent keeps calling done without a verification that "
                                "passes - the success condition cannot be confirmed on screen"
                            )
                            resumed_note = await self._escalate_and_wait(
                                run, transcript, session, step, reason, goal, history, log,
                                handoff_wait_s, last_call,
                            )
                            if resumed_note is None:
                                break
                            note, last_sig, repeats, done_nudged = resumed_note, None, 0, 0
                            deadline = time.time() + self.cfg.run_timeout_seconds
                            step += 1
                            continue
                        note = (
                            "Not done. Before done you MUST call assert_state with the goal's "
                            "success condition (text_present of the exact confirmation wording on "
                            "the CURRENT screen) and it must return ok. If that assert_state "
                            "FAILS, the goal is not achieved - do not call done, re-observe or "
                            "call stuck. Never report a value you cannot see on screen."
                        )
                        step += 1
                        continue
                    # A supplied input param that was NEVER typed/selected means
                    # the agent skipped part of the task. Push back ONCE (the
                    # other loop guards — no-progress, stuck-nudge, assert-twice-
                    # completes — keep this from wedging), then let `done` through.
                    _skip = {"branch", "tenant", "operator", "password"}
                    form_params = {
                        k for k, v in params.items()
                        if k not in _skip and len(str(v)) >= 4
                    }
                    missed = sorted(form_params - used_params)
                    if missed and unused_param_nudged < 1:
                        unused_param_nudged += 1
                        history.append(f"done -> REJECTED: params never entered: {missed}")
                        note = (
                            "You have not entered these supplied values into a field yet: "
                            f"{', '.join(missed)}. Each one belongs in a field the goal named. "
                            "Go back to the form, type/select each, submit, then done."
                        )
                        step += 1
                        continue
                    transcript.done_outputs = dict(call.args.get("outputs", {}))
                    transcript.final_state = state
                    run.status = RunStatus.COMPLETED
                    run.detail = "goal achieved"
                    log.checkpoint(step, True, "agent declared done")
                    log.run_finished("completed", outputs=list(transcript.done_outputs))
                    break

                if call.tool == "stuck":
                    reason = call.args.get("reason", "unspecified")
                    # Don't escalate a run that is actually making progress: the
                    # model sometimes calls stuck right after a string of
                    # SUCCESSFUL actions (confused by an earlier failure it has
                    # since worked around). Push back once, telling it to
                    # re-verify — but only twice, then honour the stuck call.
                    if step - last_ok_step <= 2 and stuck_nudged < 2:
                        stuck_nudged += 1
                        note = (
                            f"You called stuck, but your last action ({last_ok_tool}) SUCCEEDED "
                            f"and you are on {state.url}. Re-observe the current screen. If the "
                            "goal is already achieved, call assert_state on its success condition "
                            "and then done. Only call stuck again if you genuinely cannot proceed."
                        )
                        log.event(step, "stuck_nudged", reason=reason, on_url=state.url)
                        step += 1
                        continue
                    resumed_note = await self._escalate_and_wait(
                        run, transcript, session, step, reason, goal, history, log,
                        handoff_wait_s, last_call,
                    )
                    if resumed_note is None:
                        break  # no operator came back - end as STUCK
                    note, last_sig, repeats = resumed_note, None, 0
                    deadline = time.time() + self.cfg.run_timeout_seconds  # fresh budget
                    continue

                # Guard a login task from undoing itself: a click aimed at a
                # sign-off / logout / cancel control means the model thinks it
                # still needs to log in when it probably already has.
                if (
                    call.tool == "click"
                    and success_check is not None
                    and re.search(r"\b(log\s?in|log\s?on|sign\s?on|sign\s?in)", goal, re.I)
                ):
                    tgt = call.args.get("target") or {}
                    tval = " ".join(str(v) for v in (tgt.values() if isinstance(tgt, dict) else [tgt])).lower()
                    if any(w in tval for w in ("sign off", "signoff", "log out", "logout", "log off", "cancel")):
                        note = (
                            f"That control ({tval.strip()}) would SIGN YOU OUT. The goal is to "
                            f"log IN, and you are on {state.url} — you have very likely already "
                            "signed on. Call assert_state on the success condition (you are off "
                            "the sign-on page, no error text) and then done. Do NOT click sign-off."
                        )
                        log.event(step, "counterproductive_click_blocked", target=tval.strip())
                        step += 1
                        continue

                # actionable tool -> build Action, guardrail, execute
                action, extract_as = self._to_action(call, params)
                target_url = call.args.get("url") if call.tool == "navigate" else state.url
                decision = self.policy.check(
                    ActionContext(
                        tenant_id=tenant,
                        action_type=_TOOL_TO_ACTION[call.tool],
                        target_url=target_url,
                        declared_risk=None,
                    )
                )
                log.guardrail(step, decision.verdict, decision.reason)

                if decision.verdict in (
                    PolicyVerdict.BLOCK,
                    *(() if confirm_risky else (PolicyVerdict.REQUIRE_CONFIRMATION,)),
                ):
                    policy_blocks += 1
                    kind = (
                        "blocked" if decision.verdict == PolicyVerdict.BLOCK
                        else "require_confirmation"
                    )
                    history.append(f"{call.tool} {kind} by guardrail: {decision.reason}")
                    transcript.entries.append(
                        TranscriptEntry(step, state, call, action, False, {kind: decision.reason}, decision.verdict)
                    )
                    # This is not something the model can fix by retrying - the
                    # route/action is off-policy or needs a human. Nudge once,
                    # then force the escalation rather than let it burn steps.
                    if policy_blocks >= 2:
                        reason = f"guardrail keeps rejecting this action: {decision.reason}"
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats, policy_blocks = resumed_note, None, 0, 0
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        step += 1
                        continue
                    note = (
                        f"That action was rejected by policy ({decision.reason}). Retrying "
                        f"the same action will NOT work. Either do something genuinely "
                        f"different that the goal allows, or call stuck now to bring in a human."
                    )
                    step += 1
                    continue

                if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION and confirm_risky:
                    log.event(step, "risk_preauthorized", reason=decision.reason)

                result = await self.adapter.execute(session, action)
                ok = result.ok
                log.action(
                    step, action.type, result.target_description, ok,
                    matched_strategy=result.matched_strategy, url_after=result.url_after,
                    error=result.error, timed_out=result.timed_out,
                )
                risk = RiskClass.RISKY_IRREVERSIBLE if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION else RiskClass.SAFE_REVERSIBLE
                entry = TranscriptEntry(
                    step, state, call, action, ok,
                    {
                        "ok": ok, "error": result.error, "timed_out": result.timed_out,
                        "matched_strategy": result.matched_strategy, "url_after": result.url_after,
                        "extracted": result.extracted, "risk_class": str(risk),
                    },
                    decision.verdict, extract_as,
                )
                transcript.entries.append(entry)

                desc = _describe_call(call)

                # Landed on a recognised business-outcome screen (no such
                # member, permission wall)? End with it now — don't let the
                # model thrash on a page whose "answer" is already final. Only
                # after a navigation, so it's one extra observe at most per hop.
                if (
                    ok
                    and call.tool in ("click", "navigate", "press_key")
                    and str(result.url_after or "") != state.url
                ):
                    bo = await self._discovery_business_outcome(session, log=log, step=step)
                    if bo is not None:
                        code, msg, phrases = bo
                        run.status = RunStatus.BUSINESS_OUTCOME
                        run.detail = f"{code}: {msg}"
                        transcript.final_state = state
                        transcript.business_outcome = (code, msg, phrases)
                        log.event(step, "business_outcome", code=code, message=msg, matched=phrases)
                        log.run_finished("business_outcome", code=code)
                        break

                if ok:
                    last_ok_tool = call.tool
                    last_ok_step = step
                    policy_blocks = 0  # progress - forget earlier guardrail rejections
                    if call.tool in ("type", "select"):
                        entered = str(call.args.get("value") or call.args.get("option") or "")
                        for pk, pv in params.items():
                            if pv and str(pv) in entered:
                                used_params.add(pk)
                if not ok:
                    history.append(f"{desc} -> FAILED: {result.error}")
                    note = f"The last action failed: {result.error}. Re-observe and adapt, or call stuck."
                    if "could not resolve target" in (result.error or ""):
                        note = (
                            f"The last action failed: {result.error}. The control could NOT be "
                            "located - it is almost certainly on screen already. Do NOT scroll. "
                            "Try a DIFFERENT identifier for the same control: its form field "
                            "name (e.g. name='address'), its placeholder text, or the visible "
                            "label text next to it. If two more tries fail, call stuck."
                        )
                elif call.tool == "extract" and result.extracted is not None:
                    val = str(result.extracted)
                    history.append(f"{desc} -> got {val[:80]!r}")
                else:
                    history.append(desc)

                # A passing assert_state is the finish line. Nudge hard toward
                # `done` so the model doesn't wander off and redo work it has
                # already completed (observed on a real the legacy console edit run).
                if ok and call.tool == "assert_state":
                    note = (
                        "That check PASSED. If it is the goal's success condition, your "
                        "NEXT call must be done with the outputs - do not click, type, "
                        "navigate or scroll again."
                    )

                # --- no-progress loop guard -------------------------------
                # An action that "succeeds" but leaves the screen exactly as it
                # was, repeated, means the run is stuck in a loop the model
                # can't see its way out of. Nudge on the 2nd repeat; hard-stop
                # on the 4th (comp-use style: don't rely on the model to bail).
                sig = f"{call.tool}|{json.dumps(call.args.get('target') or call.args.get('url') or call.args.get('key') or '', sort_keys=True)}|{result.url_after}"
                if ok and sig == last_sig:
                    repeats += 1
                else:
                    repeats = 0
                last_sig = sig if ok else last_sig

                # --- cyclic-thrash guard ---------------------------------
                # The model can loop on a set of actions (type email, type
                # phone, type email, ...) so no single one repeats *consecutively*
                # but the run still goes nowhere. Count total repeats of this
                # (tool|target) and intervene once it's clearly stuck.
                tsig = f"{call.tool}|{json.dumps(call.args.get('target') or '', sort_keys=True)}"
                # `extract`/`scroll` loops (extract -> scroll -> extract -> ...)
                # dodge the consecutive guard the same way an A/B type cycle does.
                if ok and call.tool in ("type", "select", "click", "extract", "scroll", "wait_for"):
                    sig_counts[tsig] = sig_counts.get(tsig, 0) + 1
                    # scroll churns fast — cap it low regardless of target
                    scroll_n = sum(v for k, v in sig_counts.items() if k.startswith("scroll|"))
                    n = sig_counts[tsig]
                    if n >= 6 or scroll_n >= 6:
                        reason = f"cyclic thrash: repeated {call.tool} with no progress ({n}x this target, {scroll_n} scrolls)"
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats, sig_counts, cycle_nudged = resumed_note, None, 0, {}, False
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        continue
                    if n >= 3 and not cycle_nudged:
                        cycle_nudged = True
                        if call.tool == "extract":
                            note = (
                                f"You have run extract on the same target {n} times and keep "
                                "re-reading it. If the value you got looks wrong (a decoy / a "
                                "different row), try a MORE SPECIFIC target for the cell you "
                                "want — e.g. a css/xpath, or a landmark that is unique to the "
                                "REAL row. Do not scroll-and-retry. If you truly cannot pick "
                                "the right cell, call stuck with that reason."
                            )
                        elif call.tool in ("type", "select"):
                            note = (
                                f"You have set this same field {n} times. It already holds your "
                                "value. Do the goal's NEXT sub-task (a different field, or submit), "
                                "then done."
                            )
                        else:
                            note = (
                                f"You have repeated {call.tool} {n} times with no progress. Try a "
                                "genuinely different action toward the goal, or call stuck."
                            )

                # The model sometimes re-asserts the same passing success check
                # instead of calling done. It has verified the goal twice —
                # that IS done. Record the assert's condition as the checkpoint.
                if ok and call.tool == "assert_state" and repeats >= 1:
                    transcript.done_outputs = {}
                    transcript.final_state = state
                    run.status = RunStatus.COMPLETED
                    run.detail = "goal achieved (success check verified)"
                    log.checkpoint(step, True, "assert_state verified twice — completing")
                    log.run_finished("completed", outputs=[])
                    break

                if repeats >= 1:
                    if call.tool == "scroll":
                        note = (
                            f"Scrolling is NOT helping — you have scrolled {repeats + 1} times "
                            f"with no change. You are on {result.url_after}. The control you want "
                            "is already loaded. Call observe, then act on a field or button BY "
                            "NAME (type into it / click it / select an option). Do not scroll again."
                        )
                    elif call.tool in ("type", "select"):
                        tgt = call.args.get("target")
                        who = tgt.get("name") or tgt.get("label") or tgt.get("near") if isinstance(tgt, dict) else tgt
                        note = (
                            f"The '{who}' field ALREADY holds the value you just entered — see "
                            "CURRENT FORM FIELD VALUES in the observation. Do NOT type into it "
                            "again. Move to the NEXT field the goal names, or if every field is "
                            "set, click the submit/save button."
                        )
                    else:
                        note = (
                            "You have already performed this exact action and the screen did not "
                            f"change — you are on {result.url_after}. Re-observe. If the goal's data "
                            "is on screen, verify with assert_state/extract and call done. Otherwise "
                            "take the NEXT step toward the goal (a different control), or call stuck."
                        )
                if repeats >= (2 if call.tool in ("type", "select") else 3):
                    reason = f"no progress: repeated {call.tool} {repeats + 1}x with no screen change"
                    resumed_note = await self._escalate_and_wait(
                        run, transcript, session, step, reason, goal, history, log,
                        handoff_wait_s, last_call,
                    )
                    if resumed_note is None:
                        break
                    note, last_sig, repeats = resumed_note, None, 0
                    deadline = time.time() + self.cfg.run_timeout_seconds
                    continue

                # --- goal checkpoint auto-complete -----------------------
                # If the caller gave a success condition, end the run the moment
                # it holds - don't wait for the model to call done.
                if ok and success_check:
                    chk = await self.adapter.execute(
                        session, Action(type=ActionType.ASSERT_STATE, condition=success_check)
                    )
                    if chk.ok:
                        transcript.done_outputs = {
                            e.extract_as: e.action_result.get("extracted")
                            for e in transcript.entries
                            if e.extract_as and e.action_result.get("extracted") is not None
                        }
                        transcript.final_state = state
                        run.status = RunStatus.COMPLETED
                        run.detail = "goal checkpoint reached"
                        log.checkpoint(step, True, "success_check satisfied")
                        log.run_finished("completed", outputs=list(transcript.done_outputs))
                        break

                step += 1

            transcript.final_state = transcript.final_state or await self.perception.observe(self.adapter, session)
            transcript.session_id = session
            run.step_count = step
            run.ended_at = time.time()

            # ST-037: a stuck run that was NOT already escalated in-loop raises an
            # intervention here and HOLDS its session for a human.
            if (
                run.status == RunStatus.STUCK
                and self.escalation is not None
                and not transcript.intervention_id
            ):
                if self.broker is not None:
                    self.broker.register_session(session, session)
                iv = await self.escalation.open_intervention(
                    run=run, session_id=session, step_index=step,
                    reason=run.detail or transcript.stuck_reason or "stuck",
                    attempting=_attempting_str(last_call, goal),
                    goal=goal, transcript_tail=history,
                )
                transcript.intervention_id = iv.intervention_id

            return run, transcript
        finally:
            if run.status == RunStatus.STUCK:
                log.event(None, "session_held", session=session, reason="stuck — awaiting escalation")
                if surface.sandbox is not None:
                    log.event(None, "sandbox_held", container=run.sandbox_container,
                              reason="stuck — awaiting operator")
            else:
                await close_run_surface(
                    adapter=self.adapter, sandbox_manager=self.sandbox_manager,
                    surface=surface, run=run, logger=log,
                )

    async def _discovery_business_outcome(
        self, session: str, *, log: Any | None = None, step: int | None = None
    ) -> tuple[str, str, list[str]] | None:
        """The current screen against the same business-outcome patterns replay
        uses (per-host library + generic not-found / permission phrasings). A
        match means the goal has a legitimate non-happy answer — end the run
        with it, don't route a human. Returns (code, message, matched_phrases).

        When `log`/`step` are given, a screenshot of the outcome screen is
        attached to that step so the report shows *why* the run ended, not the
        pre-navigation form."""
        from ..conditions import evaluate as eval_condition
        from ..models import Condition
        from ..outcomes import business_outcomes_for

        rules = list(business_outcomes_for(getattr(self, "_run_target", "") or ""))
        rules += [
            ("member_not_found", ["No member records matched", "no members matched",
                                  "was not found", "no such member", "record not found"]),
            ("permission_denied", ["is not authorized to perform this function",
                                   "do not have permission", "authorization required",
                                   "access denied"]),
        ]  # generic fallbacks appended as (code, phrases) tuples
        try:
            obs_kw = (
                {"sink": log.sink, "run_id": log.run_id, "step": step}
                if log is not None
                else {}
            )
            state = await self.perception.observe(self.adapter, session, **obs_kw)
        except Exception:  # noqa: BLE001
            return None
        haystack = f"{state.title}\n{state.ax_summary}\n{state.dom_excerpt}".lower()
        for r in rules:
            if isinstance(r, tuple):
                code, phrases = r
                cond = Condition(kind="text_present", params={"any": phrases})
                msg = f"the app reported: {phrases[0]}"
            else:
                code, cond, msg = r.code, r.when, (r.message or r.code)
                phrases = list(cond.params.get("any") or ([cond.params["text"]] if cond.params.get("text") else []))
            try:
                if await eval_condition(cond, state):
                    hit = [p for p in phrases if p and p.lower() in haystack] or phrases[:1]
                    return code, msg, hit
            except Exception:  # noqa: BLE001
                continue
        return None

    async def _escalate_and_wait(
        self, run, transcript, session, step, reason, goal, history, log, wait_s,
        last_call: ToolCall | None = None,
    ) -> str | None:
        """Pause discovery: raise an intervention, hold the session, and BLOCK
        until an operator hands control back. Returns a resume `note` for the
        agent, or None if there is no escalation path / no operator ever came."""
        from ..models import InterventionStatus

        # Before treating this as "needs a human": is the screen a recognised
        # BUSINESS OUTCOME? "no member records matched" is a legitimate answer
        # ("member 12345 doesn't exist"), not something an operator can fix.
        bo = await self._discovery_business_outcome(session, log=log, step=step)
        if bo is not None:
            code, msg, phrases = bo
            run.status = RunStatus.BUSINESS_OUTCOME
            run.detail = f"{code}: {msg}"
            transcript.stuck_reason = None
            transcript.business_outcome = (code, msg, phrases)
            log.event(step, "business_outcome", code=code, message=msg, matched=phrases,
                      detail="recognised at discovery — ending run, no handoff")
            log.run_finished("business_outcome", code=code)
            return None

        run.status = RunStatus.STUCK
        run.detail = reason
        transcript.stuck_reason = reason
        log.stuck(step, reason)

        if self.escalation is None:
            return None

        attempting = _attempting_str(last_call, goal)
        r = reason.lower()
        terminal = any(m in r for m in (
            "already exists", "no new account was created", "cannot be undone",
            "was not found", "no such", "does not exist", "has timed out",
            "session has expired", "not authorized",
        ))

        if self.broker is not None:
            self.broker.register_session(session, session)
        iv = await self.escalation.open_intervention(
            run=run, session_id=session, step_index=step,
            reason=reason, attempting=attempting, goal=goal, transcript_tail=history,
        )
        transcript.intervention_id = iv.intervention_id

        if terminal:
            # the goal is unreachable from here and no operator action changes
            # that — end now and release the sandbox instead of holding it.
            try:
                self.escalation.abandon(iv.intervention_id, "unreachable goal — not an actionable handoff")
            except Exception:  # noqa: BLE001
                pass
            run.status = RunStatus.DEAD_END
            run.detail = f"dead end at step {step}: {reason}"
            log.event(step, "handoff_skipped", intervention_id=iv.intervention_id,
                      detail="stuck reason is not operator-actionable — ending run")
            log.run_finished("dead_end", reason=run.detail)
            return None

        if wait_s <= 0:
            return None  # no in-loop wait (tests / non-interactive callers)
        log.event(step, "awaiting_operator", intervention_id=iv.intervention_id, wait_s=wait_s)

        end = time.time() + wait_s
        while time.time() < end:
            await asyncio.sleep(1.5)
            try:
                cur = self.escalation.get(iv.intervention_id)
            except Exception:  # noqa: BLE001
                break
            if cur.status == InterventionStatus.RESOLVED:
                acts = getattr(cur, "human_actions_log", []) or []
                summary = "; ".join(
                    f"{a.get('type')}({a.get('target') or a.get('value') or a.get('url') or ''})".strip("()")
                    for a in acts
                ) or "no explicit actions recorded"
                run.status = RunStatus.RUNNING
                run.detail = None
                log.event(step, "operator_handed_back", intervention_id=iv.intervention_id, actions=summary)
                return (
                    f"An operator took control at step {step} and has now handed it back. "
                    f"What the operator did: {summary}. "
                    "Do NOT assume the goal is finished. Call observe first to re-read the "
                    "CURRENT screen, work out exactly what state the page is in now, then "
                    "continue toward the goal from here. Only call done after verifying the "
                    "success condition with assert_state / extract."
                )
        # The wait expired. If a human claimed it, give them ONE more window to
        # finish, then abandon regardless — a claimed-but-never-resolved
        # intervention must not hold a sandbox forever.
        try:
            final = self.escalation.get(iv.intervention_id)
        except Exception:  # noqa: BLE001
            final = None
        if final is not None and final.status == InterventionStatus.CLAIMED:
            log.event(step, "handoff_timeout", intervention_id=iv.intervention_id,
                      detail=f"operator in control — one more {wait_s:.0f}s grace window")
            grace_end = time.time() + wait_s
            while time.time() < grace_end:
                await asyncio.sleep(1.5)
                try:
                    cur = self.escalation.get(iv.intervention_id)
                except Exception:  # noqa: BLE001
                    break
                if cur.status == InterventionStatus.RESOLVED:
                    run.status = RunStatus.RUNNING
                    run.detail = None
                    return (
                        f"An operator handed control back at step {step}. Call observe "
                        "first, then continue toward the goal; verify with assert_state / "
                        "extract before done."
                    )
        try:
            self.escalation.abandon(iv.intervention_id, f"no resolution within {2 * wait_s:.0f}s")
        except Exception:  # noqa: BLE001
            pass
        run.status = RunStatus.DEAD_END
        run.detail = f"stuck at step {step} and no operator responded within {wait_s:.0f}s"
        log.event(step, "handoff_timeout", intervention_id=iv.intervention_id,
                  detail="unclaimed - ending run, releasing sandbox")
        log.run_finished("dead_end", reason=run.detail)
        return None

    # -- helpers ---------------------------------------------------
    async def _decide_with_backoff(
        self, goal, state, history, step, step_budget, params, note, ceiling, log
    ) -> ToolCall | None:
        """Rate limits are transient — keep rotating and retrying with a capped
        backoff (up to `ceiling` rounds). Out-of-credits is not: it propagates
        so the run stops rather than spinning."""
        backoff = 3.0
        for attempt in range(ceiling):
            try:
                return await self.agent.decide(
                    goal, state, history, steps_left=step_budget - step, params=params,
                    note=note, logger=log,
                )
            except AllProvidersOutOfBalance:
                raise
            except AllProvidersExhausted as exc:
                log.event(step, "all_providers_exhausted", attempt=attempt + 1, backoff_s=backoff, detail=str(exc))
                if attempt == ceiling - 1:
                    return None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                # re-arm the cooled-down providers for a fresh attempt (a
                # rate-limited provider recovers; a disabled one stays out).
                self.router.reset()
        return None

    @staticmethod
    def _to_action(call: ToolCall, params: dict[str, Any]) -> tuple[Action, str | None]:
        a = call.args
        t = call.tool
        if t == "click":
            return Action(type=ActionType.CLICK, target_description=a["target"]), None
        if t == "type":
            return Action(type=ActionType.TYPE, target_description=a["target"], value=str(a.get("value", ""))), None
        if t == "select":
            return Action(type=ActionType.SELECT, target_description=a["target"], value=str(a.get("option", ""))), None
        if t == "navigate":
            return Action(type=ActionType.NAVIGATE, value=a["url"]), None
        if t == "wait_for":
            return Action(type=ActionType.WAIT_FOR, condition=a.get("condition", {}), timeout_ms=int(a.get("timeout_ms", 15000))), None
        if t == "extract":
            return (
                Action(type=ActionType.EXTRACT, target_description=a["target"], expected_shape=a.get("expected_shape", "string")),
                a.get("as"),
            )
        if t == "assert_state":
            return Action(type=ActionType.ASSERT_STATE, condition=a.get("condition", {})), None
        if t == "scroll":
            tgt = {"text": a["to_text"]} if a.get("to_text") else None
            return Action(type=ActionType.SCROLL, target_description=tgt, value=str(a.get("direction", "down"))), None
        if t == "press_key":
            return Action(type=ActionType.PRESS_KEY, value=str(a.get("key", "Enter"))), None
        raise ValueError(f"non-actionable tool: {t}")


def _describe_call(call: ToolCall) -> str:
    a = call.args
    if call.tool == "type":
        return f"type into {a.get('target')} (value hidden)"
    if call.tool == "navigate":
        return f"navigate {a.get('url')}"
    if call.tool == "extract":
        return f"extract {a.get('as')} <- {a.get('target')} as {a.get('expected_shape')}"
    return f"{call.tool} {a.get('target') or a.get('condition') or ''}".strip()


def _attempting_str(call: ToolCall | None, goal: str | None) -> str | None:
    """A plain-language line for the operator: what the agent was trying to do
    when it gave up. Its stated intent + the concrete control/value it wanted."""
    if call is None:
        return f"work toward the goal: {goal}" if goal else None
    a = call.args
    target = a.get("target") or a.get("url") or a.get("condition") or a.get("key")
    verb = {
        "click": "click", "type": "type into", "select": "pick an option in",
        "navigate": "navigate to", "wait_for": "wait for", "extract": "read",
        "assert_state": "verify", "scroll": "scroll to", "press_key": "press key on",
    }.get(call.tool, call.tool)
    val = ""
    if call.tool == "type":
        val = " (a value it was given; hidden here)"
    elif call.tool in ("select", "press_key", "scroll") and a.get("value"):
        val = f" = {a['value']!r}"
    piece = f"{verb} {target}{val}".strip()
    intent = (call.reasoning or "").strip().rstrip(".")
    if intent:
        return f"{intent} — by trying to {piece}"
    return f"trying to {piece}"
