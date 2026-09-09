"""Condition evaluation — shared by wait_for, assert_state, step checkpoints,
business-outcome rules and recoverable-condition rules.

Kept deliberately small and text/structure based so the *same* evaluator runs at
discovery time and replay time. Element presence needs a live probe (an async
callable the caller supplies from its adapter); everything else works off the
normalized `SurfaceState`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from .models import Condition, SurfaceState

Probe = Callable[[dict[str, Any]], Awaitable[bool]]

# A non-empty value of roughly the right shape — enough to catch "" / "N/A" /
# "An error occurred" landing where a real read should be. Deliberately
# permissive: this checks the *shape*, never a per-run value. Shared by the
# recorder (to build `extract_matches` checkpoints) and the replay executor
# (to enforce `x-shape`, which JSON-Schema draft 2020-12 ignores).
SHAPE_PATTERNS: dict[str, str] = {
    "currency": r"[\$£€]?\s?-?[\d,]+\.\d{2}",
    "number": r"-?[\d,]+(\.\d+)?",
    "integer": r"-?\d[\d,]*",
    "date": r"(\d{1,4}[-/]\d{1,2}[-/]\d{1,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    "boolean": r"(?i:true|false|yes|no|enabled|disabled)",
}


def shape_pattern(shape: str) -> str:
    """Regex that a value of `shape` must contain, or `\\S` (just non-empty)."""
    return SHAPE_PATTERNS.get(shape, r"\S")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


async def evaluate(
    cond: Condition,
    state: SurfaceState,
    *,
    probe: Probe | None = None,
    extracted: dict[str, Any] | None = None,
) -> bool:
    p = cond.params
    kind = cond.kind
    # Collapse all whitespace runs to single spaces before substring matching:
    # legacy table markup renders `<td>Name:</td><td>Ada</td>` as "Name:\tAda"
    # or "Name:\nAda", so an assertion of "Name: Ada" would never match the raw
    # text. Normalising both sides makes "Label: Value" checkpoints robust.
    haystack = _norm_ws(f"{state.title}\n{state.ax_summary}\n{state.dom_excerpt}")

    if kind in {"all_of", "any_of"}:
        subs = [
            c if isinstance(c, Condition) else Condition(**c)
            for c in p.get("conditions", [])
        ]
        if not subs:
            return False
        results = [
            await evaluate(c, state, probe=probe, extracted=extracted) for c in subs
        ]
        return all(results) if kind == "all_of" else any(results)
    if kind == "url_matches":
        return re.search(p.get("pattern", ".*"), state.url) is not None
    if kind == "text_present":
        needles = p.get("any") or [p.get("text", "")]
        return any(n and _norm_ws(n).lower() in haystack.lower() for n in needles)
    if kind == "text_absent":
        needles = p.get("any") or [p.get("text", "")]
        return all(not n or _norm_ws(n).lower() not in haystack.lower() for n in needles)
    if kind == "element_present":
        if probe is None:
            return False
        return await probe(p.get("target", p))
    if kind == "element_absent":
        if probe is None:
            return True
        return not await probe(p.get("target", p))
    if kind in {"extract_equals", "extract_matches"}:
        if extracted is None:
            return False
        field = p.get("field")
        value = extracted.get(field) if field else None
        if value is None:
            return False
        if kind == "extract_equals":
            return str(value).strip() == str(p.get("value", "")).strip()
        return re.search(p.get("pattern", ".*"), str(value)) is not None
    raise ValueError(f"unknown condition kind: {kind}")


def describe(cond: Condition) -> str:
    return cond.description or f"{cond.kind}({cond.params})"
