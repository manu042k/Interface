"""ST-018: Discovery Agent — the fixed tool vocabulary and the decide() call.

The model never gets code execution or a raw-selector tool. It gets exactly the
10 structured tools below, and its output is always coerced to one schema-valid
`ToolCall` from that set. One reject-and-retry on a malformed / out-of-vocab
response, then we treat it as `stuck` rather than looping on garbage.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm.router import LLMRouter
from ..models import SurfaceState, ToolCall

VOCAB = {
    "observe", "click", "type", "select", "navigate",
    "wait_for", "extract", "assert_state", "done", "stuck",
}


def _tool(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"reasoning": {"type": "string", "description": "why this action, in one sentence"}, **props},
                "required": ["reasoning", *required],
            },
        },
    }


_TARGET = {
    "type": "object",
    "description": "how to find the control: any of role+name, label, placeholder, text, near (row label), css",
    "properties": {
        "role": {"type": "string"},
        "name": {"type": "string"},
        "label": {"type": "string"},
        "placeholder": {"type": "string"},
        "text": {"type": "string"},
        "near": {"type": "string"},
        "css": {"type": "string"},
    },
}

TOOL_SCHEMA: list[dict[str, Any]] = [
    _tool("observe", "Re-read the current screen before deciding.", {}, []),
    _tool("click", "Click a control.", {"target": _TARGET}, ["target"]),
    _tool("type", "Type text into a field.", {"target": _TARGET, "value": {"type": "string"}}, ["target", "value"]),
    _tool("select", "Choose an option in a dropdown/radio.", {"target": _TARGET, "option": {"type": "string"}}, ["target", "option"]),
    _tool("navigate", "Go to a URL on the allowlisted target app.", {"url": {"type": "string"}}, ["url"]),
    _tool("wait_for", "Wait (bounded) for a state condition.",
          {"condition": {"type": "object"}, "timeout_ms": {"type": "integer"}}, ["condition"]),
    _tool("extract", "Read a value off the screen with an expected shape.",
          {"target": _TARGET, "expected_shape": {"type": "string", "enum": ["string", "number", "currency", "integer", "boolean", "date"]},
           "as": {"type": "string", "description": "output field name"}}, ["target", "expected_shape", "as"]),
    _tool("assert_state", "Verify a condition holds mid-flow or as the checkpoint.",
          {"condition": {"type": "object"}}, ["condition"]),
    _tool("done", "Goal achieved. Return the typed outputs.", {"outputs": {"type": "object"}}, ["outputs"]),
    _tool("stuck", "Cannot safely proceed. Escalate to a human.",
          {"reason": {"type": "string"}, "context": {"type": "object"}}, ["reason"]),
]

SYSTEM_PROMPT = """You operate a legacy back-office banking web app by driving its UI, the way a human operator would.
You are in the DISCOVERY phase: figure out how to accomplish the goal once, carefully. Your run will be recorded and replayed deterministically, so prefer stable, minimal steps.

Rules:
- Every turn, respond with exactly ONE tool call from the provided set. No prose.
- Ground each decision in the CURRENT screen state you are given; call observe if you are unsure.
- Identify controls by role+name, label, visible text, or row label ("near") — not by guessing CSS.
- Never enter real credentials or invent data. Use only values from the goal/params.
- Bounded waits only. If a control is missing or the screen is unexpected and you cannot safely proceed, call stuck with a clear reason.
- When the goal's success condition is visibly true, call assert_state to check it, then done with the extracted outputs.
"""


class DiscoveryAgent:
    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def decide(
        self,
        goal: str,
        state: SurfaceState,
        history: list[str],
        *,
        steps_left: int,
        params: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> ToolCall:
        user = self._render_user(goal, state, history, steps_left, params, note)
        resp = await self._router.call(SYSTEM_PROMPT, user, TOOL_SCHEMA)
        call = self._coerce(resp.tool, resp.args, resp.reasoning)
        if call is not None:
            return call

        # one corrective retry
        retry_user = user + (
            f"\n\nYOUR LAST RESPONSE WAS INVALID (tool={resp.tool!r}). "
            f"Respond with exactly one tool call from the allowed set: {sorted(VOCAB)}."
        )
        resp2 = await self._router.call(SYSTEM_PROMPT, retry_user, TOOL_SCHEMA)
        call = self._coerce(resp2.tool, resp2.args, resp2.reasoning)
        if call is not None:
            return call
        return ToolCall(
            tool="stuck",
            args={"reason": f"model produced an out-of-vocabulary response twice ({resp.tool!r}, {resp2.tool!r})", "context": {}},
            reasoning="invalid model output",
        )

    # -- internals ---------------------------------------------------
    @staticmethod
    def _coerce(tool: str, args: dict[str, Any], reasoning: str) -> ToolCall | None:
        if tool not in VOCAB:
            return None
        if not isinstance(args, dict):
            return None
        try:
            return ToolCall(tool=tool, args=args, reasoning=reasoning or args.get("reasoning", ""))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _render_user(
        goal: str,
        state: SurfaceState,
        history: list[str],
        steps_left: int,
        params: dict[str, Any] | None,
        note: str | None,
    ) -> str:
        hist = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(history[-12:])) or "  (none yet)"
        parts = [
            f"GOAL: {goal}",
            f"PARAMS: {json.dumps(params or {})}",
            f"STEPS REMAINING: {steps_left}",
            "",
            "CURRENT SCREEN",
            f"  url:   {state.url}",
            f"  title: {state.title}",
            "  accessibility outline:",
            _indent(state.ax_summary, 4),
            "  dom outline:",
            _indent(state.dom_excerpt, 4),
            "",
            "ACTION HISTORY:",
            hist,
        ]
        if note:
            parts += ["", f"NOTE FROM THE SYSTEM: {note}"]
        return "\n".join(parts)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in (text or "").splitlines()[:60])
