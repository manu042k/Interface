"""ST-019/020: the discovery Orchestrator.

State machine for one discovery run: observe -> decide -> guardrail-check -> act,
until done / stuck / max-steps / timeout. Guardrail rejections are fed back to
the agent as an observation (not silently dropped). `stuck` and
`all_providers_exhausted` both route to a pause-and-hold path — the session is
kept open for Phase 8 escalation, never torn down.
"""

from __future__ import annotations

import asyncio
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

        from ..sandbox.session import close_run_surface, open_run_surface

        surface = await open_run_surface(
            adapter=self.adapter, sandbox_manager=self.sandbox_manager,
            target=target, tenant=tenant, run=run, logger=log,
        )
        session = surface.session_handle
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
                    transcript.done_outputs = dict(call.args.get("outputs", {}))
                    transcript.final_state = state
                    run.status = RunStatus.COMPLETED
                    run.detail = "goal achieved"
                    log.checkpoint(step, True, "agent declared done")
                    log.run_finished("completed", outputs=list(transcript.done_outputs))
                    break

                if call.tool == "stuck":
                    reason = call.args.get("reason", "unspecified")
                    transcript.stuck_reason = reason
                    transcript.final_state = state
                    run.status = RunStatus.STUCK
                    run.detail = reason
                    log.stuck(step, reason)
                    break

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
                history.append(desc if ok else f"{desc} -> FAILED: {result.error}")
                if not ok:
                    note = f"The last action failed: {result.error}. Re-observe and adapt, or call stuck."
                step += 1

            transcript.final_state = transcript.final_state or await self.perception.observe(self.adapter, session)
            transcript.session_id = session
            run.step_count = step
            run.ended_at = time.time()

            # ST-037: a stuck run raises an intervention with full context and
            # HOLDS its session (not torn down) so a human can take over exactly
            # where automation stopped.
            if run.status == RunStatus.STUCK and self.escalation is not None:
                if self.broker is not None:
                    self.broker.register_session(session, session)
                iv = await self.escalation.open_intervention(
                    run=run, session_id=session, step_index=step,
                    reason=run.detail or transcript.stuck_reason or "stuck",
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
