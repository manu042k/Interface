"""ST-013/014: consistent structured-event shapes + a RunLogger convenience.

Every component logs through the same helpers so a run's `events.jsonl` reads as
one coherent story: what was attempted, why (model reasoning for discovery; the
artifact step for replay), and the outcome. Evidence capture (ST-014) is gated to
failure points and explicit checkpoints — never every step.
"""

from __future__ import annotations

import time
from typing import Any

from .observability import Sink


class RunLogger:
    def __init__(self, sink: Sink, run_id: str) -> None:
        self.sink = sink
        self.run_id = run_id

    def event(self, step: int | None, kind: str, **fields: Any) -> None:
        self.sink.log_event(self.run_id, step, {"event": kind, **fields})

    def run_started(self, mode: str, target: str, goal: str | None = None) -> None:
        self.event(None, "run_started", mode=mode, target=target, goal=goal, ts=time.time())

    def run_finished(self, status: str, **fields: Any) -> None:
        self.event(None, "run_finished", status=status, **fields)

    def decision(self, step: int, tool: str, args: dict[str, Any], reasoning: str) -> None:
        # ST-013: the model's stated reasoning is captured with the action.
        # ST-015: a free-typed value may be sensitive and unrecognizable to the
        # pattern redactor (a raw password has no delimiter) — mask it here by
        # construction, keeping only a length hint for debugging.
        self.event(step, "decision", tool=tool, args=_mask_typed_value(tool, args), reasoning=reasoning)

    def action(self, step: int, action_type: str, target: str, ok: bool, **fields: Any) -> None:
        self.event(step, "action", action_type=action_type, target=target, ok=ok, **fields)

    def guardrail(self, step: int, verdict: str, reason: str) -> None:
        self.event(step, "guardrail", verdict=verdict, reason=reason)

    def checkpoint(self, step: int | None, ok: bool, description: str) -> None:
        self.event(step, "checkpoint", ok=ok, description=description)

    def recoverable(self, step: int, rule: str, action: str) -> None:
        self.event(step, "recoverable_condition", rule=rule, recovery=action)

    def business_outcome(self, step: int, code: str, message: str) -> None:
        self.event(step, "business_outcome", code=code, message=message)

    def stuck(self, step: int, reason: str) -> None:
        self.event(step, "stuck", reason=reason)

    # ST-014: richer signal, only on failure / explicit checkpoint.
    def evidence_screenshot(self, step: int | None, png: bytes, meta: dict[str, Any] | None = None) -> str:
        return self.sink.put_evidence(self.run_id, step, "screenshot", png, meta)

    def evidence_dom(self, step: int | None, html: str, meta: dict[str, Any] | None = None) -> str:
        return self.sink.put_evidence(self.run_id, step, "dom", html.encode("utf-8"), meta)


def _mask_typed_value(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool != "type" or "value" not in args:
        return args
    val = args.get("value")
    masked = dict(args)
    masked["value"] = f"«typed:len={len(val)}»" if isinstance(val, str) else "«typed»"
    return masked
