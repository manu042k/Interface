"""ST-038/039: Operator Console (mocked UI, REAL mechanism).

The UI is a thin API — no co-browsing front-end (out of scope per brief §3.6).
The *mechanism* is real: the operator claims an intervention, takes the control
lock on the SAME live session the automation was using, drives it through the
adapter, every action is recorded to the intervention's `human_actions_log`, and
releasing the lock signals resume.
"""

from __future__ import annotations

from typing import Any

from ..models import ActionType
from ..surface.base import Action, SurfaceAdapter
from .service import EscalationService


class OperatorConsole:
    def __init__(self, *, escalation: EscalationService, adapter: SurfaceAdapter) -> None:
        self._esc = escalation
        self._adapter = adapter
        self._sessions: dict[str, str] = {}  # intervention_id -> session_id under human control

    # -- view / claim ------------------------------------------
    def inbox(self, status: str = "open") -> list[dict[str, Any]]:
        return [
            {
                "intervention_id": iv.intervention_id,
                "tenant": iv.tenant_id,
                "capability": iv.capability_name,
                "goal": iv.goal,
                "run_id": iv.run_id,
                "step_index": iv.step_index,
                "reason": iv.reason,
                "opened_at": iv.opened_at,
                "status": iv.status,
            }
            for iv in self._esc.list_interventions(status=status)
        ]

    def context(self, intervention_id: str) -> dict[str, Any]:
        iv = self._esc.get(intervention_id)
        return {
            "intervention_id": iv.intervention_id,
            "goal": iv.goal,
            "reason": iv.reason,
            "step_index": iv.step_index,
            "screenshot_ref": iv.context.get("screenshot_ref"),
            "current_url": iv.context.get("current_url"),
            "transcript_tail": iv.context.get("transcript_tail"),
        }

    def claim(self, intervention_id: str, operator: str) -> dict[str, Any]:
        iv = self._esc.claim(intervention_id, operator)
        return {"intervention_id": iv.intervention_id, "status": iv.status, "claimed_by": operator}

    # -- ST-039: take control of the SAME session -----------------
    def take_control(self, intervention_id: str, operator: str) -> dict[str, Any]:
        handle = self._esc.take_control(intervention_id, operator)
        self._sessions[intervention_id] = handle["session_id"]
        return handle

    async def perform(self, intervention_id: str, operator: str, action: dict[str, Any]) -> dict[str, Any]:
        """Drive the live session as the human. Every action is recorded."""
        session_id = self._sessions.get(intervention_id)
        if session_id is None:
            raise ValueError("take control before performing actions")
        act = Action(
            type=ActionType(action["type"]),
            target_description=action.get("target"),
            value=action.get("value"),
            condition=action.get("condition"),
        )
        result = await self._adapter.execute(session_id, act)
        record = {
            "type": action["type"],
            "target": action.get("target"),
            "ok": result.ok,
            "url_after": result.url_after,
            "error": result.error,
            "by": operator,
        }
        self._esc.record_human_action(intervention_id, record)
        return record

    # -- ST-040: hand back --------------------------------------
    async def release_control(
        self, intervention_id: str, operator: str, *, goal_checkpoint: Any | None = None
    ) -> dict[str, Any]:
        self._esc.record_human_action(intervention_id, {"type": "release_control", "by": operator})
        outcome = await self._esc.resume(intervention_id, goal_checkpoint=goal_checkpoint)
        self._sessions.pop(intervention_id, None)
        return {
            "resumed": outcome.resumed,
            "checkpoint_already_holds": outcome.checkpoint_already_holds,
            "detail": outcome.detail,
        }
