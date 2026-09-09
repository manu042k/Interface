"""ST-030..ST-035: Replay Executor — the production execution path.

For each recorded step:  resolve locator (ranked chain) -> guardrail -> act ->
verify step checkpoint. No LLM anywhere in this loop (ADR-07).

Before acting on each step, and again whenever a checkpoint fails, the current
screen is matched against the artifact's declared exceptional-state rules:

  * a `known_outcome` match  -> STOP, return {outcome: business_outcome, code}
                                (a legitimate answer, never logged as an error)
  * a `recoverable_rule` match -> dismiss / wait / reload, log it, retry the step
  * anything else that breaks a checkpoint -> {outcome: hard_failure,
                                failure_detail:{step_index, expected, observed}}
                                with screenshot + DOM evidence attached.

Non-idempotent steps get a checkpoint re-check before any retry, so an ambiguous
failure after a submit never double-submits (ST-035).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from jsonschema import Draft202012Validator

from ..conditions import evaluate as eval_condition
from ..events import RunLogger
from ..models import (
    ActionType,
    CapabilityArtifact,
    Condition,
    FailureDetail,
    ReplayOutcome,
    ReplayResult,
    Step,
    SurfaceState,
)
from ..policy.engine import ActionContext, PolicyEngine, PolicyVerdict
from ..surface.base import Action, SurfaceAdapter
from ..surface.perception import Perception
from .locator import LocatorResolutionEngine

_RETRYABLE_CEILING = 3


class ReplayError(RuntimeError):
    pass


class ReplayExecutor:
    def __init__(
        self,
        *,
        adapter: SurfaceAdapter,
        perception: Perception,
        policy: PolicyEngine,
        locator_engine: LocatorResolutionEngine,
        logger_factory: Any,
        sandbox_manager: Any | None = None,
        escalation: Any | None = None,
        broker: Any | None = None,
        watch_delay_ms: int = 0,
    ) -> None:
        self.adapter = adapter
        self.perception = perception
        self.policy = policy
        self.locators = locator_engine
        self._logger_factory = logger_factory
        self.sandbox_manager = sandbox_manager
        # Pace a *sandboxed* replay so its live noVNC feed is watchable; 0 and/or
        # a headless replay run flat out.
        self.watch_delay_ms = watch_delay_ms
        # Optional human-in-the-loop path for a replay that hits an
        # unrecoverable condition (brief §3.6): route an intervention, hold the
        # live session for a human, then resume. When unset, a hard failure is
        # terminal (the offline default used by tests / the CLI).
        self.escalation = escalation
        self.broker = broker

    # -- ST-030 boundary validation ------------------------------------
    @staticmethod
    def validate_params(artifact: CapabilityArtifact, params: dict[str, Any]) -> list[str]:
        validator = Draft202012Validator(artifact.input_schema)
        return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in validator.iter_errors(params)]

    async def execute(
        self,
        artifact: CapabilityArtifact,
        params: dict[str, Any],
        *,
        target: str,
        tenant: str = "default",
        run_id: str,
        idempotency_key: str | None = None,
        run: Any | None = None,
        handoff_wait_s: float = 0.0,
    ) -> ReplayResult:
        started = time.time()
        log: RunLogger = self._logger_factory(run_id)
        log.run_started("replay", target, artifact.goal_description)
        log.event(None, "replay_started", artifact_id=artifact.artifact_id, version=artifact.version,
                  idempotency_key=idempotency_key)

        errs = self.validate_params(artifact, params)
        if errs:
            log.run_finished("failed", reason="param_schema", errors=errs)
            return ReplayResult(
                outcome=ReplayOutcome.HARD_FAILURE,
                failure_detail=FailureDetail(step_index=-1, expected="params match input_schema", observed="; ".join(errs)),
                duration_seconds=time.time() - started,
            )

        outputs: dict[str, Any] = {}
        recovered: list[str] = []

        from ..models import RunMode, RunRecord
        from ..sandbox.session import close_run_surface, open_run_surface

        run = run or RunRecord(run_id=run_id, mode=RunMode.REPLAY, tenant_id=tenant, app_target=target)
        surface = await open_run_surface(
            adapter=self.adapter, sandbox_manager=self.sandbox_manager,
            target=target, tenant=tenant, run=run, logger=log,
        )
        session = surface.session_handle

        # Watch pacing: only when this replay actually has a live sandbox feed.
        watch_s = (self.watch_delay_ms / 1000) if surface.sandbox is not None else 0.0
        if watch_s > 0:
            # give the noVNC iframe time to connect before anything moves
            warmup = max(watch_s, 4.0)
            log.event(None, "watch_warmup", description=f"holding {warmup:.0f}s for the live feed to connect")
            await asyncio.sleep(warmup)

        try:
            for step in artifact.steps:
                if watch_s > 0 and step.step_index > 0:
                    await asyncio.sleep(watch_s)
                state = await self.perception.observe(self.adapter, session)

                bo = await self._match_business_outcome(artifact, state, step.step_index)
                if bo is not None:
                    log.business_outcome(step.step_index, bo.code, bo.message)
                    log.run_finished("business_outcome", code=bo.code)
                    return ReplayResult(
                        outcome=ReplayOutcome.BUSINESS_OUTCOME,
                        business_outcome_code=bo.code,
                        recovered_conditions=recovered,
                        steps_executed=step.step_index,
                        duration_seconds=time.time() - started,
                    )

                state, did_recover = await self._maybe_recover(artifact, session, state, step.step_index, log)
                recovered.extend(did_recover)

                res = await self._run_step(artifact, step, session, state, params, outputs, log, recovered)
                if res is not None and res.outcome == ReplayOutcome.HARD_FAILURE:
                    # brief §3.6: an unrecoverable replay condition is an
                    # escalation trigger, not just a stop. Route an intervention,
                    # hold the live session for a human, then resume from here.
                    if await self._escalate_replay(
                        run, artifact, session, step, res, log, params, outputs, recovered, handoff_wait_s
                    ):
                        recovered.append("human_intervention")
                        if run is not None:
                            run.step_count = step.step_index + 1
                        continue
                if res is not None:
                    res.recovered_conditions = recovered
                    res.duration_seconds = time.time() - started
                    return res
                if run is not None:
                    run.step_count = step.step_index + 1

            # -- final checkpoint --------------------------------
            if watch_s > 0:
                await asyncio.sleep(watch_s)  # let the last action's result land on screen
            final_state = await self.perception.observe(self.adapter, session)
            ok = await self._check(artifact.checkpoint, final_state, session, outputs)
            log.checkpoint(None, ok, artifact.checkpoint.description or artifact.checkpoint.kind)
            if not ok:
                return await self._hard_failure(
                    session, log, step_index=len(artifact.steps) - 1,
                    expected=f"final checkpoint: {artifact.checkpoint.description or artifact.checkpoint.kind}",
                    observed=f"url={final_state.url}", started=started, recovered=recovered,
                )

            out_errs = self._validate_outputs(artifact, outputs)
            if out_errs:
                return await self._hard_failure(
                    session, log, step_index=len(artifact.steps) - 1,
                    expected="outputs match output_schema", observed="; ".join(out_errs),
                    started=started, recovered=recovered,
                )

            outcome = ReplayOutcome.RECOVERABLE_THEN_SUCCESS if recovered else ReplayOutcome.SUCCESS
            log.run_finished(str(outcome), outputs=list(outputs))
            return ReplayResult(
                outcome=outcome, outputs=outputs, recovered_conditions=recovered,
                steps_executed=len(artifact.steps), duration_seconds=time.time() - started,
            )
        finally:
            await close_run_surface(
                adapter=self.adapter, sandbox_manager=self.sandbox_manager,
                surface=surface, run=run, logger=log,
            )

    # -- one step -------------------------------------------------
    async def _run_step(
        self,
        artifact: CapabilityArtifact,
        step: Step,
        session: str,
        state: SurfaceState,
        params: dict[str, Any],
        outputs: dict[str, Any],
        log: RunLogger,
        recovered: list[str],
    ) -> ReplayResult | None:
        """Returns a ReplayResult to stop the run, or None to continue."""
        concrete_target: dict[str, Any] | None = None
        matched_strategy: str | None = None

        if step.locator_spec:
            res = await self.locators.resolve(
                step.locator_spec, self.adapter, session,
                artifact_version=artifact.version, surface_fingerprint=state.fingerprint,
                step_index=step.step_index,
            )
            log.event(step.step_index, "locator_resolution", **res.as_event())
            if not res.ok:
                return await self._hard_failure(
                    session, log, step_index=step.step_index,
                    expected=f"resolve a control for: {step.description}",
                    observed=res.error or "unresolvable", started=0.0, recovered=recovered, no_duration=True,
                )
            concrete_target = res.concrete
            matched_strategy = res.matched_strategy

        # guardrail
        target_url = self._step_url(step, params) or state.url
        decision = self.policy.check(ActionContext(
            tenant_id=artifact.tenant_scope.tenant_id or "default",
            action_type=step.action_type,
            target_url=target_url,
            declared_risk=step.risk_class,
        ))
        log.guardrail(step.step_index, decision.verdict, decision.reason)
        if decision.verdict == PolicyVerdict.BLOCK:
            return await self._hard_failure(
                session, log, step_index=step.step_index,
                expected="action permitted by guardrail", observed=f"blocked: {decision.reason}",
                started=0.0, recovered=recovered, no_duration=True,
            )
        if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION:
            # The human review that promoted this artifact to APPROVED is the
            # confirmation for its risky steps; unattended replay of a
            # non-approved artifact is refused at the Gateway (ST-025).
            log.event(step.step_index, "risk_preauthorized_by_approval", reason=decision.reason)

        action = self._to_action(step, concrete_target, params)

        attempts = 0
        while True:
            attempts += 1
            result = await self.adapter.execute(session, action)
            log.action(step.step_index, step.action_type, result.target_description, result.ok,
                       matched_strategy=matched_strategy or result.matched_strategy,
                       url_after=result.url_after, error=result.error,
                       timed_out=result.timed_out, attempt=attempts)

            if step.action_type == ActionType.EXTRACT and result.ok and step.output_binding:
                outputs[step.output_binding.field] = result.extracted

            post_state = await self.perception.observe(self.adapter, session)

            # a failure might actually be a declared business outcome
            bo = await self._match_business_outcome(artifact, post_state, step.step_index)
            if bo is not None:
                log.business_outcome(step.step_index, bo.code, bo.message)
                log.run_finished("business_outcome", code=bo.code)
                return ReplayResult(
                    outcome=ReplayOutcome.BUSINESS_OUTCOME, business_outcome_code=bo.code,
                    steps_executed=step.step_index,
                )

            checkpoint_ok = True
            if step.step_checkpoint is not None:
                checkpoint_ok = await self._check(step.step_checkpoint, post_state, session, outputs)
                log.checkpoint(step.step_index, checkpoint_ok, step.step_checkpoint.description or step.step_checkpoint.kind)

            if result.ok and checkpoint_ok:
                return None  # step done, continue

            # try a recoverable condition
            post_state, did_recover = await self._maybe_recover(artifact, session, post_state, step.step_index, log)
            if did_recover:
                recovered.extend(did_recover)
                if attempts <= _RETRYABLE_CEILING:
                    continue

            # ST-035: non-idempotent step — re-check before retrying
            if not step.idempotent:
                recheck = await self._check(step.step_checkpoint, post_state, session, outputs) if step.step_checkpoint else result.ok
                log.event(step.step_index, "idempotency_recheck", checkpoint_now=recheck)
                if recheck:
                    return None  # it actually went through; do NOT resubmit
                return await self._hard_failure(
                    session, log, step_index=step.step_index,
                    expected=_expected_str(step), observed=_observed_str(result, post_state),
                    started=0.0, recovered=recovered, no_duration=True,
                )

            # idempotent step: bounded retry with backoff
            if attempts <= _RETRYABLE_CEILING and (result.timed_out or not result.ok or not checkpoint_ok):
                await asyncio.sleep(0.3 * attempts)
                continue

            return await self._hard_failure(
                session, log, step_index=step.step_index,
                expected=_expected_str(step), observed=_observed_str(result, post_state),
                started=0.0, recovered=recovered, no_duration=True,
            )

    # -- exceptional-state matching ---------------------------------
    async def _match_business_outcome(self, artifact, state, step_index):
        for rule in artifact.known_outcomes:
            if step_index < rule.from_step:
                continue
            if await eval_condition(rule.when, state):
                return rule
        return None

    async def _maybe_recover(self, artifact, session, state, step_index, log) -> tuple[SurfaceState, list[str]]:
        applied: list[str] = []
        for rule in artifact.recoverable_rules:
            attempts = 0
            while await eval_condition(rule.when, state) and attempts < rule.max_attempts:
                attempts += 1
                log.recoverable(step_index, rule.name, rule.action)
                if rule.action == "dismiss" and rule.target:
                    r = await self.locators.resolve(
                        rule.target, self.adapter, session,
                        artifact_version=artifact.version, surface_fingerprint=state.fingerprint,
                        step_index=step_index,
                    )
                    if r.ok:
                        await self.adapter.execute(session, Action(type=ActionType.CLICK, target_description=r.concrete))
                elif rule.action == "reload":
                    await self.adapter.execute(session, Action(type=ActionType.NAVIGATE, value=state.url))
                # 'wait' just falls through to the settle poll
                if rule.settle is not None:
                    await self.adapter.execute(session, Action(
                        type=ActionType.WAIT_FOR,
                        condition={"kind": rule.settle.kind, "params": rule.settle.params},
                        timeout_ms=rule.timeout_ms,
                    ))
                state = await self.perception.observe(self.adapter, session)
                applied.append(rule.name)
        return state, applied

    # -- helpers -------------------------------------------------
    async def _check(self, cond: Condition | None, state: SurfaceState, session: str, outputs: dict[str, Any]) -> bool:
        if cond is None:
            return True

        async def probe(target: dict[str, Any]) -> bool:
            return await self.adapter.probe(session, target)

        return await eval_condition(cond, state, probe=probe, extracted=outputs)

    def _validate_outputs(self, artifact: CapabilityArtifact, outputs: dict[str, Any]) -> list[str]:
        schema = artifact.output_schema
        try:
            validator = Draft202012Validator(schema)
        except Exception:  # noqa: BLE001
            return []
        return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in validator.iter_errors(outputs)]

    def _step_url(self, step: Step, params: dict[str, Any]) -> str | None:
        if step.action_type != ActionType.NAVIGATE or step.value_binding is None:
            return None
        return self._resolve_value(step.value_binding, params)

    def _to_action(self, step: Step, concrete_target: dict[str, Any] | None, params: dict[str, Any]) -> Action:
        t = step.action_type
        if t == ActionType.NAVIGATE:
            return Action(type=t, value=self._resolve_value(step.value_binding, params))
        if t == ActionType.WAIT_FOR:
            c = step.step_checkpoint
            cond = {"kind": c.kind, "params": c.params} if c else {}
            return Action(type=t, condition=cond, timeout_ms=step.wait_timeout_ms)
        if t == ActionType.ASSERT_STATE:
            c = step.step_checkpoint
            return Action(type=t, condition={"kind": c.kind, "params": c.params} if c else {})
        if t == ActionType.EXTRACT:
            return Action(type=t, target_description=concrete_target, expected_shape=step.output_binding.shape if step.output_binding else "string")
        value = self._resolve_value(step.value_binding, params) if step.value_binding else None
        if t == ActionType.SCROLL and value and value not in {"down", "up", "top", "bottom"}:
            # a recorded scroll-to-text step
            return Action(type=t, target_description={"text": value}, value="down")
        return Action(type=t, target_description=concrete_target, value=value)

    @staticmethod
    def _resolve_value(binding, params: dict[str, Any]) -> str:
        if binding is None:
            return ""
        if binding.param is not None:
            if binding.param not in params:
                raise ReplayError(f"missing required param {binding.param!r}")
            return str(params[binding.param])
        text = binding.literal or ""
        # {param} templating for URLs
        return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m.group(1), m.group(0))), text)

    async def _escalate_replay(
        self, run, artifact, session, step, failure, log, params, outputs, recovered, wait_s,
    ) -> bool:
        """Pause replay on an unrecoverable step: raise an intervention, hold the
        live session, and BLOCK until a human hands control back (brief §3.6).

        Returns True if, after the hand-back, the step's expected state now holds
        (the human completed it) so replay can continue; False if there is no
        escalation path, no operator came, or the step is still failing.
        """
        from ..models import InterventionStatus, RunMode, RunRecord, RunStatus

        if self.escalation is None or wait_s <= 0 or run is None:
            return False

        fd = failure.failure_detail
        reason = (
            f"replay stuck at step {step.step_index} ({step.action_type.value}): "
            f"expected {fd.expected if fd else _expected_str(step)}, "
            f"observed {fd.observed if fd else 'unknown'}"
        )
        bound = ""
        if step.value_binding is not None:
            bound = (
                f" the value of param '{step.value_binding.param}'"
                if step.value_binding.param else " a recorded value"
            )
        attempting = f"step {step.step_index}: {step.action_type.value} - {step.description}{bound}"
        run.status = RunStatus.STUCK
        run.detail = reason
        log.stuck(step.step_index, reason)

        if self.broker is not None:
            try:
                self.broker.register_session(session, session)
            except Exception:  # noqa: BLE001
                pass

        iv_run = run if isinstance(run, RunRecord) else RunRecord(
            run_id=getattr(run, "run_id", "replay"), mode=RunMode.REPLAY,
            tenant_id=artifact.tenant_scope.tenant_id or "default", app_target=run.app_target,
        )
        iv = await self.escalation.open_intervention(
            run=iv_run, session_id=session, step_index=step.step_index,
            reason=reason, attempting=attempting, capability_name=artifact.name,
            goal=artifact.goal_description,
            transcript_tail=[f"recovered: {c}" for c in recovered],
        )
        log.event(step.step_index, "awaiting_operator", intervention_id=iv.intervention_id, wait_s=wait_s)

        end = time.time() + wait_s
        while time.time() < end:
            await asyncio.sleep(1.5)
            try:
                cur = self.escalation.get(iv.intervention_id)
            except Exception:  # noqa: BLE001
                break
            if cur.status != InterventionStatus.RESOLVED:
                continue

            acts = getattr(cur, "human_actions_log", []) or []
            summary = "; ".join(
                f"{a.get('type')}({a.get('target') or a.get('value') or a.get('url') or ''})".strip("()")
                for a in acts
            ) or "no explicit actions recorded"
            log.event(step.step_index, "operator_handed_back",
                      intervention_id=iv.intervention_id, actions=summary)
            run.status = RunStatus.RUNNING
            run.detail = None

            # Did the human actually get us past this step? Re-observe and
            # verify rather than assume — same discipline as the rest of replay.
            post = await self.perception.observe(self.adapter, session)
            if step.step_checkpoint is not None:
                ok = await self._check(step.step_checkpoint, post, session, outputs)
                log.event(step.step_index, "post_handback_checkpoint", ok=ok)
                if ok:
                    return True
            # no checkpoint, or it doesn't hold yet — try the recorded step once more
            retry = await self._run_step(artifact, step, session, post, params, outputs, log, recovered)
            log.event(step.step_index, "post_handback_retry",
                      ok=retry is None or retry.outcome != ReplayOutcome.HARD_FAILURE)
            if retry is None:
                return True
            if retry.outcome == ReplayOutcome.BUSINESS_OUTCOME:
                failure.outcome = retry.outcome
                failure.business_outcome_code = retry.business_outcome_code
                failure.failure_detail = None
            return False

        log.event(step.step_index, "handoff_timeout", intervention_id=iv.intervention_id)
        return False

    async def _hard_failure(
        self, session, log, *, step_index, expected, observed, started, recovered, no_duration=False
    ) -> ReplayResult:
        refs: list[str] = []
        try:
            snap = await self.adapter.snapshot(session)
            if snap.screenshot_png:
                refs.append(log.evidence_screenshot(step_index, snap.screenshot_png, {"url": snap.url}))
            if snap.html:
                refs.append(log.evidence_dom(step_index, snap.html, {"url": snap.url}))
        except Exception:  # noqa: BLE001
            pass
        log.event(step_index, "hard_failure", expected=expected, observed=observed, evidence=refs)
        log.run_finished("hard_failure", step_index=step_index)
        return ReplayResult(
            outcome=ReplayOutcome.HARD_FAILURE,
            failure_detail=FailureDetail(step_index=step_index, expected=expected, observed=observed, evidence_refs=refs),
            evidence_refs=refs,
            recovered_conditions=recovered,
            steps_executed=step_index,
            duration_seconds=0.0 if no_duration else time.time() - started,
        )


def _expected_str(step: Step) -> str:
    if step.step_checkpoint:
        return f"after {step.action_type.value}: {step.step_checkpoint.description or step.step_checkpoint.kind}"
    return f"{step.action_type.value} '{step.description}' succeeds"


def _observed_str(result, state: SurfaceState) -> str:
    bits = [f"action_ok={result.ok}"]
    if result.error:
        bits.append(f"error={result.error!r}")
    if result.timed_out:
        bits.append("timed_out")
    bits.append(f"url={state.url}")
    return ", ".join(bits)
