"""ST-037/040: Escalation Service.

detect stuck  -> open an InterventionRequest carrying enough context to act on
                 (capability/goal, step, screenshot, reason), and HOLD the
                 automation lock so session state is exactly as automation left it
human acts    -> control transfers to the operator on the SAME session
resume        -> control returns to automation; if the goal checkpoint already
                 holds, the run is marked resolved, else automation continues
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from ..events import RunLogger
from ..models import InterventionRequest, InterventionStatus, RunRecord
from ..surface.base import SurfaceAdapter
from ..surface.perception import Perception
from .session_broker import Holder, Lease, SessionBroker

_FIELD_TAG_RE = re.compile(r"<(?:input|select|textarea)\b[^>]*>", re.I)
_NAME_ATTR_RE = re.compile(r'\bname=["\']([^"\']+)["\']', re.I)
_VALUE_ATTR_RE = re.compile(r'\bvalue=["\']([^"\']*)["\']', re.I)
_SELECTED_OPTION_RE = re.compile(r'<option\b(?=[^>]*\bselected\b)[^>]*?\bvalue=["\']([^"\']*)["\']', re.I)


def _form_value_sig(html: str) -> str:
    """A cheap, order-stable signature of every form field's current value —
    used to notice that a human changed something DIRECTLY in the live browser
    (clicks that never went through the recorded operator-actions API), even
    though the individual gesture can't be itemised. Not a security signature,
    just a diff key. Attribute order within a tag doesn't matter (name= and
    value= are searched independently, not matched as one sequential pattern)."""
    parts: list[str] = []
    for tag in _FIELD_TAG_RE.findall(html or ""):
        nm = _NAME_ATTR_RE.search(tag)
        if not nm:
            continue
        vm = _VALUE_ATTR_RE.search(tag)
        parts.append(f"{nm.group(1)}={vm.group(1) if vm else ''}")
    parts.extend(f"selected={m}" for m in _SELECTED_OPTION_RE.findall(html or ""))
    return "|".join(parts)


@dataclass
class ResumeOutcome:
    resumed: bool
    checkpoint_already_holds: bool
    detail: str


class EscalationService:
    def __init__(
        self,
        *,
        broker: SessionBroker,
        adapter: SurfaceAdapter,
        perception: Perception,
        logger_factory: Any,
    ) -> None:
        self.broker = broker
        self.adapter = adapter
        self.perception = perception
        self._logger_factory = logger_factory
        self._interventions: dict[str, InterventionRequest] = {}
        self._auto_leases: dict[str, Lease] = {}  # session_id -> automation's held lease

    # -- ST-037: detect & route --------------------------------------
    async def open_intervention(
        self,
        *,
        run: RunRecord,
        session_id: str,
        step_index: int,
        reason: str,
        attempting: str | None = None,
        capability_name: str | None = None,
        goal: str | None = None,
        transcript_tail: list[str] | None = None,
    ) -> InterventionRequest:
        log: RunLogger = self._logger_factory(run.run_id)

        # hold the automation lock so nothing else touches the session
        try:
            lease = self.broker.acquire(session_id, Holder.AUTOMATION)
            self._auto_leases[session_id] = lease
        except Exception as exc:  # noqa: BLE001
            log.event(step_index, "escalation_lock_warning", detail=str(exc))

        # capture the context bundle
        screenshot_ref = None
        current_url = ""
        form_sig = ""
        try:
            snap = await self.adapter.snapshot(session_id)
            current_url = snap.url
            form_sig = _form_value_sig(snap.html)
            if snap.screenshot_png:
                screenshot_ref = log.evidence_screenshot(step_index, snap.screenshot_png, {"url": snap.url})
            if snap.html:
                log.evidence_dom(step_index, snap.html, {"url": snap.url})
        except Exception as exc:  # noqa: BLE001
            log.event(step_index, "escalation_snapshot_warning", detail=str(exc))

        iv = InterventionRequest(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            capability_name=capability_name,
            goal=goal or run.goal,
            step_index=step_index,
            reason=reason,
            attempting=attempting,
            context={
                "screenshot_ref": screenshot_ref,
                "transcript_tail": (transcript_tail or [])[-8:],
                "current_url": current_url,
                "session_id": session_id,
                "attempting": attempting,
                # baseline for resume()'s passive change-detection — a human
                # driving the live browser directly (noVNC) never calls the
                # recorded operator-actions API, so this is the only way to
                # notice they actually did something before hand-back.
                "form_sig": form_sig,
            },
        )
        self._interventions[iv.intervention_id] = iv
        log.event(
            step_index, "intervention_opened", intervention_id=iv.intervention_id,
            reason=reason, attempting=attempting,
        )
        return iv

    async def open_risk_approval(
        self,
        *,
        run: RunRecord,
        session_id: str,
        step_index: int,
        proposed_action: str,
        reason: str,
        goal: str | None = None,
        capability_name: str | None = None,
        transcript_tail: list[str] | None = None,
    ) -> InterventionRequest:
        """A risk-approval gate: the run is NOT stuck, but the next action is
        risky/irreversible and must be signed off (Approve / Reject) BEFORE it
        runs. Automation pauses; no takeover. Captures a screenshot for context."""
        log: RunLogger = self._logger_factory(run.run_id)
        try:
            lease = self.broker.acquire(session_id, Holder.AUTOMATION)
            self._auto_leases[session_id] = lease
        except Exception as exc:  # noqa: BLE001
            log.event(step_index, "escalation_lock_warning", detail=str(exc))

        screenshot_ref = None
        current_url = ""
        try:
            snap = await self.adapter.snapshot(session_id)
            current_url = snap.url
            if snap.screenshot_png:
                screenshot_ref = log.evidence_screenshot(step_index, snap.screenshot_png, {"url": snap.url})
        except Exception as exc:  # noqa: BLE001
            log.event(step_index, "escalation_snapshot_warning", detail=str(exc))

        iv = InterventionRequest(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            capability_name=capability_name,
            goal=goal or run.goal,
            step_index=step_index,
            kind="risk_approval",
            proposed_action=proposed_action,
            reason=reason,
            attempting=proposed_action,
            context={
                "screenshot_ref": screenshot_ref,
                "transcript_tail": (transcript_tail or [])[-8:],
                "current_url": current_url,
                "session_id": session_id,
            },
        )
        self._interventions[iv.intervention_id] = iv
        log.event(step_index, "risk_approval_requested", intervention_id=iv.intervention_id,
                  proposed_action=proposed_action, reason=reason)
        return iv

    def decide(self, intervention_id: str, *, approved: bool, operator: str, note: str = "") -> InterventionRequest:
        """Resolve a risk_approval gate. Releases automation's lease so the run
        can proceed (approved) or wind down (rejected)."""
        iv = self.get(intervention_id)
        if iv.kind != "risk_approval":
            raise ValueError(f"intervention {intervention_id} is a {iv.kind}, not a risk_approval")
        if iv.status == InterventionStatus.RESOLVED:
            raise ValueError(f"intervention {intervention_id} is already resolved")
        session_id = iv.context.get("session_id")
        auto = self._auto_leases.pop(session_id, None) if session_id else None
        if auto is not None:
            try:
                self.broker.release(auto)
            except Exception:  # noqa: BLE001
                pass
        iv.decision = "approved" if approved else "rejected"
        iv.claimed_by = operator
        iv.status = InterventionStatus.RESOLVED
        iv.resolved_at = time.time()
        iv.resolution = f"{iv.decision} by {operator}" + (f": {note}" if note else "")
        self._logger_factory(iv.run_id).event(
            iv.step_index, "risk_approval_decided", intervention_id=intervention_id,
            decision=iv.decision, by=operator, note=note or None,
        )
        return iv

    # -- queries --------------------------------------------------
    def list_interventions(self, status: str | None = None) -> list[InterventionRequest]:
        items = list(self._interventions.values())
        if status:
            items = [i for i in items if i.status == status]
        return sorted(items, key=lambda i: i.opened_at)

    def get(self, intervention_id: str) -> InterventionRequest:
        if intervention_id not in self._interventions:
            raise KeyError(f"no such intervention: {intervention_id}")
        return self._interventions[intervention_id]

    def abandon(self, intervention_id: str, reason: str = "no operator responded") -> InterventionRequest:
        """Close an unclaimed intervention because the wait for a human expired.
        Releases automation's held lease so the session/sandbox can be reclaimed."""
        iv = self.get(intervention_id)
        session_id = iv.context.get("session_id")
        auto = self._auto_leases.pop(session_id, None) if session_id else None
        if auto is not None:
            try:
                self.broker.release(auto)
            except Exception:  # noqa: BLE001
                pass
        iv.status = InterventionStatus.RESOLVED
        iv.resolved_at = time.time()
        iv.resolution = f"abandoned: {reason}"
        self._logger_factory(iv.run_id).event(
            iv.step_index, "intervention_abandoned", intervention_id=intervention_id, reason=reason
        )
        return iv

    # -- ST-038/039: claim + control transfer -----------------------
    def claim(self, intervention_id: str, operator: str) -> InterventionRequest:
        iv = self.get(intervention_id)
        if iv.status != InterventionStatus.OPEN:
            raise ValueError(f"intervention {intervention_id} is {iv.status}, not open")
        iv.status = InterventionStatus.CLAIMED
        iv.claimed_by = operator
        iv.claimed_at = time.time()
        self._logger_factory(iv.run_id).event(iv.step_index, "intervention_claimed", by=operator)
        return iv

    def take_control(self, intervention_id: str, operator: str) -> dict[str, Any]:
        """Transfer the lock to the human on the SAME live session."""
        iv = self.get(intervention_id)
        if iv.claimed_by != operator:
            raise ValueError("claim the intervention before taking control")
        session_id = iv.context["session_id"]

        # release automation's lease, grant the human's
        auto = self._auto_leases.pop(session_id, None)
        if auto is not None:
            try:
                self.broker.release(auto)
            except Exception:  # noqa: BLE001
                pass
        human_lease = self.broker.acquire(session_id, Holder.HUMAN, ttl=600)
        self._human_lease = human_lease

        handle = self.broker.session_handle(session_id) or session_id
        self._logger_factory(iv.run_id).event(iv.step_index, "control_transferred", to="human", operator=operator)
        return {
            "session_id": session_id,
            "live_handle": handle,
            "remote_display": f"cdp://{handle}",  # a real CDP endpoint is wired at the adapter in prod
            "lease_token": human_lease.token,
            "current_url": iv.context.get("current_url"),
        }

    def record_human_action(self, intervention_id: str, action: dict[str, Any]) -> None:
        iv = self.get(intervention_id)
        entry = {"ts": time.time(), **action}
        iv.human_actions_log.append(entry)
        self._logger_factory(iv.run_id).event(iv.step_index, "human_action", **action)

    # -- ST-040: resume ------------------------------------------
    async def resume(
        self, intervention_id: str, *, goal_checkpoint: Any | None = None, resolution: str = "handed back"
    ) -> ResumeOutcome:
        iv = self.get(intervention_id)
        session_id = iv.context["session_id"]

        # release human, re-acquire automation
        human = getattr(self, "_human_lease", None)
        if human is not None and human.session_id == session_id:
            try:
                self.broker.release(human)
            except Exception:  # noqa: BLE001
                pass
        auto_lease = self.broker.acquire(session_id, Holder.AUTOMATION)
        self._auto_leases[session_id] = auto_lease

        # Passively detect what the human actually did — they may have driven
        # the live browser directly (noVNC) rather than through the recorded
        # operator-actions API, so human_actions_log would otherwise show
        # nothing but this release_control. Diff against the baseline snapshot
        # taken when the intervention opened: a URL change is a navigation, a
        # form-value change is an edit. Best-effort; a diff we can't take just
        # means less is registered, never an error.
        try:
            after = await self.adapter.snapshot(session_id)
            before_url = iv.context.get("current_url", "")
            if after.url and before_url and after.url != before_url:
                self.record_human_action(intervention_id, {
                    "type": "navigate", "detail": f"{before_url} -> {after.url}", "by": "detected",
                })
            before_sig = iv.context.get("form_sig", "")
            after_sig = _form_value_sig(after.html)
            if before_sig and after_sig and before_sig != after_sig:
                self.record_human_action(intervention_id, {
                    "type": "edit", "detail": "form field values changed on screen", "by": "detected",
                })
        except Exception:  # noqa: BLE001
            pass

        checkpoint_holds = False
        if goal_checkpoint is not None:
            from ..conditions import evaluate as eval_condition

            state = await self.perception.observe(self.adapter, session_id)

            async def probe(t: dict[str, Any]) -> bool:
                return await self.adapter.probe(session_id, t)

            checkpoint_holds = await eval_condition(goal_checkpoint, state, probe=probe)

        iv.status = InterventionStatus.RESOLVED
        iv.resolved_at = time.time()
        iv.resolution = resolution + ("; goal already satisfied" if checkpoint_holds else "")
        self._logger_factory(iv.run_id).event(
            iv.step_index, "intervention_resolved", checkpoint_holds=checkpoint_holds, resolution=iv.resolution
        )
        return ResumeOutcome(
            resumed=True,
            checkpoint_already_holds=checkpoint_holds,
            detail=iv.resolution,
        )
