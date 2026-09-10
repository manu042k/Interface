"""Record-time capability summary.

One plain-text model call, made once when a discovery run is recorded — NOT in
replay. Produces a short reference blurb an AI agent (or a human) reads to decide
whether to invoke the capability. Best-effort: if the model is unavailable the
catalog falls back to the raw goal description.
"""

from __future__ import annotations

from ..models import CapabilityArtifact
from ..redaction import redact_text

_SYSTEM = (
    "You write terse reference blurbs for a catalog of automation capabilities "
    "that AI agents invoke by name. Present tense, third person, no preamble, no "
    "markdown headers. Describe ONLY what the given goal / inputs / outputs / "
    "outcomes state — never invent parameters, return values, or behaviour that "
    "is not listed."
)


def _prompt(a: CapabilityArtifact) -> str:
    steps = "\n".join(
        f"  {s.step_index}. {s.action_type.value} — {s.description}" for s in a.steps
    )
    inputs = ", ".join(
        f"{k}:{v.get('type', 'string')}" for k, v in a.input_schema.get("properties", {}).items()
    ) or "none"
    outputs = ", ".join(
        f"{k}:{v.get('x-shape', v.get('type', 'string'))}"
        for k, v in a.output_schema.get("properties", {}).items()
    ) or "none"
    outcomes = ", ".join(r.code for r in a.known_outcomes) or "none"
    return (
        f"Goal it was recorded for: {a.goal_description}\n"
        f"App: {a.vendor_app_id} v{a.app_version}\n"
        f"Inputs: {inputs}\nOutputs: {outputs}\n"
        f"Known business outcomes it can report: {outcomes}\n"
        f"Risk class: {a.risk_class.value}\n"
        f"Recorded steps:\n{steps}\n\n"
        "Write 2-3 sentences: what this capability does, when an agent should call "
        "it, what it returns, and any caveat (risk, outcomes to branch on). "
        "Do not restate the step list."
    )


async def summarize(artifact: CapabilityArtifact, router) -> str:
    try:
        text = await router.call_text(_SYSTEM, _prompt(artifact))
    except Exception:  # noqa: BLE001 — a missing summary is never fatal
        return ""
    text = (text or "").strip()
    if not text:
        return ""
    return redact_text(text)[0][:800]
