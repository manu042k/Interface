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
    "wait_for", "extract", "assert_state", "scroll", "press_key",
    "done", "stuck",
}


def _merge_usage(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Sum the token counters from two OpenAI-style usage dicts."""
    out = dict(a)
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = (b or {}).get(k)
        if isinstance(v, int):
            out[k] = out.get(k, 0) + v
    return out


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
    _tool("assert_state",
          "Verify a condition holds on the CURRENT screen (a checkpoint). Cannot read an "
          "<input> value or save a form. condition must be one of: "
          '{"kind":"text_present","params":{"text":"CHANGES SAVED"}}, '
          '{"kind":"text_absent","params":{"text":"..."}}, '
          '{"kind":"url_matches","params":{"pattern":"/members/\\\\d+$"}}, '
          '{"kind":"element_present","params":{"target":{...}}}.',
          {"condition": {"type": "object"}}, ["condition"]),
    _tool("scroll", "Scroll the page when the control or content you need is off-screen.",
          {"direction": {"type": "string", "enum": ["down", "up", "top", "bottom"]},
           "to_text": {"type": "string", "description": "optional: scroll until this visible text is in view"}},
          ["direction"]),
    _tool("press_key", "Press a keyboard key - Enter/Tab/Escape, or a function key like F7 that legacy consoles use for navigation.",
          {"key": {"type": "string"}}, ["key"]),
    _tool("done",
          "Goal achieved - return the typed outputs. ONLY valid immediately after an "
          "assert_state that succeeded: that assertion is what gets recorded as the "
          "replay checkpoint. Never call done straight after a click/type.",
          {"outputs": {"type": "object"}}, ["outputs"]),
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
- If the control or value you need is below the fold, scroll first. Do not repeat the same extract - once you have read a value it is captured; move on.
- assert_state checks a condition on the SCREEN (a heading/text is present, the URL matches). It does NOT save a form and it cannot read an <input> field's value - never use it to "confirm" an edit you have not submitted yet.

Finishing (mandatory):
- You may NEVER call done as your first reaction to a click/type succeeding. Finishing is two calls: (1) assert_state with the goal's success condition, phrased as a concrete screen check - prefer text_present of the exact confirmation wording you can see (e.g. {"kind":"text_present","params":{"text":"CHANGES SAVED"}}), else url_matches; then (2), only if that assert_state returned ok, done with the outputs.
- That assert_state is recorded verbatim as the replay checkpoint, so make it specific: a phrase that is on the success screen and NOT on the form/other screens. A bare url_matches of the page you are already on is a weak checkpoint - avoid it when there is confirmation text.
- If the assert_state fails, you are not done: re-observe and figure out what is still missing.

Editing / updating a record (important):
- The flow is: type into each field -> click the form's Save / Submit / Update / Confirm button -> WAIT for the result screen -> assert_state the confirmation -> done.
- Typing a value into a field changes nothing until you click that button. Do not assert_state or navigate away before clicking it.
- Success is the app's own confirmation after the save: a "Changes saved" / "... UPDATED" / "... has been updated" screen, or the record page now showing the new value. assert_state that exact text, then done. If you typed the fields but never saw a save button, scroll the form to find it before giving up.

Progress discipline (important):
- Check "CURRENT FORM FIELD VALUES" and the ACTION HISTORY before each step. If a field already holds the value you need, DO NOT type it again — move to the next control (e.g. click the submit/search/save button).
- Never repeat the same action twice in a row. If your last action succeeded, the next action must advance the flow (submit, navigate, open a result, extract).
- One field per type call; after filling the inputs a form needs, click its submit control.
- If an unexpected modal / notice / interstitial blocks the flow (e.g. a "Session Notice", cookie banner, confirmation dialog), dismiss it via its own continue/OK/acknowledge control — do NOT click site navigation to escape it.
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
        logger: Any | None = None,
    ) -> ToolCall:
        user = self._render_user(goal, state, history, steps_left, params, note)
        resp = await self._router.call(SYSTEM_PROMPT, user, TOOL_SCHEMA, logger=logger)
        call = self._coerce(resp.tool, resp.args, resp.reasoning)
        if call is not None:
            call.usage = _merge_usage({}, resp.usage)
            return call

        # one corrective retry
        retry_user = user + (
            f"\n\nYOUR LAST RESPONSE WAS INVALID (tool={resp.tool!r}). "
            f"Respond with exactly one tool call from the allowed set: {sorted(VOCAB)}."
        )
        resp2 = await self._router.call(SYSTEM_PROMPT, retry_user, TOOL_SCHEMA, logger=logger)
        call = self._coerce(resp2.tool, resp2.args, resp2.reasoning)
        combined = _merge_usage(resp.usage, resp2.usage)
        if call is not None:
            call.usage = combined
            return call
        return ToolCall(
            tool="stuck",
            args={"reason": f"model produced an out-of-vocabulary response twice ({resp.tool!r}, {resp2.tool!r})", "context": {}},
            reasoning="invalid model output",
            usage=combined,
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
