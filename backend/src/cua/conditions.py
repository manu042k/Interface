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


async def evaluate(
    cond: Condition,
    state: SurfaceState,
    *,
    probe: Probe | None = None,
    extracted: dict[str, Any] | None = None,
) -> bool:
    p = cond.params
    kind = cond.kind
    haystack = f"{state.title}\n{state.ax_summary}\n{state.dom_excerpt}"

    if kind == "url_matches":
        return re.search(p.get("pattern", ".*"), state.url) is not None
    if kind == "text_present":
        needles = p.get("any") or [p.get("text", "")]
        return any(n and n.lower() in haystack.lower() for n in needles)
    if kind == "text_absent":
        needles = p.get("any") or [p.get("text", "")]
        return all(not n or n.lower() not in haystack.lower() for n in needles)
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
