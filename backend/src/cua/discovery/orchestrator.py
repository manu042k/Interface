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
        deadline = time.time() + self.cfg.run_timeout_seconds
        history: list[str] = []
        note: str | None = None
        last_call: ToolCall | None = None  # for "what the agent was attempting" on escalation
        last_ok_tool: str | None = None  # last action that actually ran, for the done gate
        done_nudged = False

        from ..sandbox.session import close_run_surface, open_run_surface

        surface = await open_run_surface(
            adapter=self.adapter, sandbox_manager=self.sandbox_manager,
            target=target, tenant=tenant, run=run, logger=log,
        )
        session = surface.session_handle
        # no-progress loop guard: (tool, target) signature + a hash of the screen
        last_sig: str | None = None
        repeats = 0
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
                    # A `done` must be earned by a preceding verification - the
                    # assert_state/extract that ran just before is what the
                    # recorder keeps as the replay checkpoint. Nudge once if the
                    # model tries to finish straight off a click/type.
                    if last_ok_tool not in ("assert_state", "extract") and not done_nudged:
                        done_nudged = True
                        note = (
                            "Not yet. Before done you must call assert_state with the goal's "
                            "success condition - prefer text_present of the exact confirmation "
                            "wording on the current screen (that assertion becomes the replay "
                            "checkpoint). If it passes, then call done."
                        )
                        history.append("done -> REJECTED: verify with assert_state first")
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
                    resumed_note = await self._escalate_and_wait(
                        run, transcript, session, step, reason, goal, history, log,
                        handoff_wait_s, last_call,
                    )
                    if resumed_note is None:
                        break  # no operator came back - end as STUCK
                    note, last_sig, repeats = resumed_note, None, 0
                    deadline = time.time() + self.cfg.run_timeout_seconds  # fresh budget
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

                if decision.verdict == PolicyVerdict.BLOCK:
                    history.append(f"{call.tool} BLOCKED by guardrail: {decision.reason}")
                    note = f"Your last action was blocked by policy: {decision.reason}. Choose a permitted action or call stuck."
                    transcript.entries.append(
                        TranscriptEntry(step, state, call, action, False, {"blocked": decision.reason}, decision.verdict)
                    )
                    step += 1
                    continue

                if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION and not confirm_risky:
                    history.append(f"{call.tool} needs human confirmation: {decision.reason}")
                    note = (
                        f"That action is risky/irreversible and needs human confirmation "
                        f"({decision.reason}). It was not performed. Call stuck to escalate, "
                        f"or choose a safe alternative."
                    )
                    transcript.entries.append(
                        TranscriptEntry(step, state, call, action, False, {"require_confirmation": decision.reason}, decision.verdict)
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
                if ok:
                    last_ok_tool = call.tool
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

                if repeats >= 1:
                    note = (
                        "You have already performed this exact action and the screen "
                        "did not change. If you have the data the goal asks for, call "
                        "done now with the outputs. Otherwise do something genuinely "
                        "different (scroll, a different control) or call stuck."
                    )
                if repeats >= 3:
                    reason = f"no progress: repeated {call.tool} 4x with no screen change"
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

    async def _escalate_and_wait(
        self, run, transcript, session, step, reason, goal, history, log, wait_s,
        last_call: ToolCall | None = None,
    ) -> str | None:
        """Pause discovery: raise an intervention, hold the session, and BLOCK
        until an operator hands control back. Returns a resume `note` for the
        agent, or None if there is no escalation path / no operator ever came."""
        from ..models import InterventionStatus

        run.status = RunStatus.STUCK
        run.detail = reason
        transcript.stuck_reason = reason
        log.stuck(step, reason)

        if self.escalation is None:
            return None

        attempting = _attempting_str(last_call, goal)

        if self.broker is not None:
            self.broker.register_session(session, session)
        iv = await self.escalation.open_intervention(
            run=run, session_id=session, step_index=step,
            reason=reason, attempting=attempting, goal=goal, transcript_tail=history,
        )
        transcript.intervention_id = iv.intervention_id
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
        log.event(step, "handoff_timeout", intervention_id=iv.intervention_id)
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
