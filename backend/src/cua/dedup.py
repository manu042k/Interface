"""Semantic duplicate detection for capabilities.

`assembly.record()` already catches a re-run that reproduced an EXISTING flow
byte-for-byte (`flow_fingerprint` match) and flags it `duplicate_of`. But two
recordings of the *same business function* phrased differently take structurally
different paths — a different nav click, one extra step, a newer outcome-library
seed — so their fingerprints differ and the exact check misses them.

This module adds a looser, post-record pass (never in the discovery/replay
loops):

  1. cheap structural signals — same entry URL, same input-schema keys, same
     risk class, same checkpoint shape, high overlap of (action, bound-param)
     pairs. All strong -> it's a duplicate, no model call.
  2. only when those are *partially* there and a router is available -> ONE
     `router.call_text` asking "same function?  SAME / DIFFERENT / UNSURE".

The result is a review signal (`duplicate_of` + a note), never an auto-delete.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .models import CapabilityArtifact

_STRONG = 0.80          # action/param Jaccard for a no-model structural match
_MAYBE = 0.45           # below this, not even worth a model call


def _entry_key(url: str) -> str:
    p = urlparse(url or "")
    return f"{(p.hostname or '').lower()}{p.path.rstrip('/')}"


def _checkpoint_shape(cond: Any) -> tuple:
    """kind + the *structure* of a condition, ignoring the literal text/values
    a checkpoint asserts (those legitimately differ between two recordings)."""
    if cond is None:
        return ()
    kind = getattr(cond, "kind", None) or (cond.get("kind") if isinstance(cond, dict) else None)
    params = getattr(cond, "params", None) or (cond.get("params") if isinstance(cond, dict) else {}) or {}
    subs = params.get("conditions") or params.get("all_of") or params.get("any_of") or []
    if subs:
        return (kind, tuple(sorted(_checkpoint_shape(s) for s in subs)))
    # leaf: keep the param *keys* (e.g. 'field', 'pattern' vs 'text') not values
    return (kind, tuple(sorted(k for k in params if k not in ("text", "any", "value", "description"))))


def _action_param_bag(a: CapabilityArtifact) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for s in a.steps:
        param = s.value_binding.param if (s.value_binding and s.value_binding.param) else None
        out.append((s.action_type.value, param))
    return out


def _jaccard(x: list, y: list) -> float:
    sx, sy = set(x), set(y)
    if not sx and not sy:
        return 1.0
    return len(sx & sy) / len(sx | sy)


def structural_overlap(a: CapabilityArtifact, b: CapabilityArtifact) -> dict[str, Any]:
    same_entry = _entry_key(a.entry_url) == _entry_key(b.entry_url)
    same_keys = set((a.input_schema or {}).get("properties", {})) == set(
        (b.input_schema or {}).get("properties", {})
    )
    same_risk = a.risk_class == b.risk_class
    same_checkpoint = _checkpoint_shape(a.checkpoint) == _checkpoint_shape(b.checkpoint)
    jac = _jaccard(_action_param_bag(a), _action_param_bag(b))
    strong = same_entry and same_keys and same_risk and same_checkpoint and jac >= _STRONG
    return {
        "same_entry": same_entry, "same_keys": same_keys, "same_risk": same_risk,
        "same_checkpoint": same_checkpoint, "action_jaccard": round(jac, 2),
        "strong": strong,
        "worth_asking": same_entry and (same_keys or jac >= _MAYBE) and not strong,
    }


_LLM_SYSTEM = (
    "You compare two recorded browser-automation capabilities for the same web app "
    "and decide whether they accomplish the SAME end-user function (same goal, same "
    "effect), even if the recorded steps differ. Reply with exactly one word on the "
    "first line: SAME, DIFFERENT, or UNSURE. Then one short sentence of reasoning."
)


def _steps_digest(a: CapabilityArtifact) -> str:
    return " -> ".join(
        f"{s.action_type.value}({s.value_binding.param})" if (s.value_binding and s.value_binding.param)
        else s.action_type.value
        for s in a.steps
    )


async def semantic_twin(
    built: CapabilityArtifact, store: Any, *, router: Any | None = None,
) -> tuple[CapabilityArtifact, str, str] | None:
    """Look for an existing capability (same vendor app, different name) that is
    the same function as `built`. Returns (twin, basis, note) or None.
    `basis` is "structural" or "llm"."""
    candidates = [
        c for c in store.list(vendor_app_id=built.vendor_app_id)
        if c.name != built.name and c.tenant_scope.kind == "base"
        and c.status in ("approved", "draft")
    ]
    # newest version per name; then consider APPROVED capabilities first so the
    # flag points at the canonical one, not another unreviewed draft.
    by_name: dict[str, CapabilityArtifact] = {}
    for c in sorted(candidates, key=lambda x: x.version):
        by_name[c.name] = c
    ordered = sorted(by_name.values(), key=lambda c: (c.status != "approved", c.name))

    ask: list[CapabilityArtifact] = []
    for cand in ordered:
        ov = structural_overlap(built, cand)
        if ov["strong"]:
            return cand, "structural", (
                f"structural match with {cand.name}@v{cand.version}: same entry URL, "
                f"same inputs, same risk, same checkpoint shape, "
                f"{int(ov['action_jaccard'] * 100)}% step overlap"
            )
        if ov["worth_asking"]:
            ask.append(cand)

    if not ask or router is None:
        return None

    for cand in ask[:3]:  # bounded: at most 3 model calls, best signals first
        user = (
            f"APP: {built.vendor_app_id}\n\n"
            f"CAPABILITY A (new)\n  goal: {built.goal_description}\n  steps: {_steps_digest(built)}\n\n"
            f"CAPABILITY B (existing: {cand.name})\n  goal: {cand.goal_description}\n  steps: {_steps_digest(cand)}\n\n"
            "Same end-user function?"
        )
        try:
            reply = (await router.call_text(_LLM_SYSTEM, user)).strip()
        except Exception:  # noqa: BLE001 - a failed check never blocks recording
            continue
        verdict = reply.split()[0].upper() if reply else ""
        if verdict == "SAME":
            reason = reply.split("\n", 1)[1].strip() if "\n" in reply else ""
            return cand, "llm", (
                f"model judged this the same function as {cand.name}@v{cand.version}"
                + (f": {reason}" if reason else "")
            )
    return None
