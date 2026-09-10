"""Drift self-healing — turn a replay that hit an UNRECOGNISED screen state into
a v+1 DRAFT capability with a candidate rule, for a human to review.

Why this exists
---------------
Replay only knows the `known_outcomes` / `recoverable_rules` that were frozen
onto the artifact at record time. When a vendor adds a new rejection page (say
"account dormant — see branch") a later replay matches nothing, breaks its
checkpoint, escalates to a human… and then the NEXT replay hits the identical
wall, because nothing learned the new state.

`propose_patch` closes that loop the safe way: it copies the artifact, appends a
candidate rule built from what replay actually saw, and saves it as a **DRAFT**
(`record_outcome="drift_patch"`, `supersedes` set). It is never auto-applied —
it shows up in the normal `/review` queue, where a reviewer sets the real code,
decides business-outcome vs recoverable, or rejects it.

No LLM here and none in the replay loop (ADR-07). The heuristic proposal is a
`text_present` rule over the page's own error text — the exact raw material a
reviewer needs; classification is theirs to make.
"""

from __future__ import annotations

import re
from typing import Any

from .models import (
    ArtifactStatus,
    BusinessOutcomeRule,
    CapabilityArtifact,
    Condition,
    DriftCandidate,
)

_uuid = __import__("uuid").uuid4


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "state"


def _rule_phrases(cond: Condition) -> list[str]:
    p = cond.params or {}
    out = list(p.get("any") or [])
    if p.get("text"):
        out.append(p["text"])
    for sub in p.get("conditions") or []:
        try:
            out += _rule_phrases(Condition(**sub) if isinstance(sub, dict) else sub)
        except Exception:  # noqa: BLE001
            pass
    return [s for s in out if isinstance(s, str)]


def _covered(artifact: CapabilityArtifact, phrases: list[str]) -> bool:
    lows = {p.lower() for p in phrases}
    for rule in list(artifact.known_outcomes) + list(artifact.recoverable_rules):
        known = {s.lower() for s in _rule_phrases(rule.when)}
        if lows & known:
            return True
    return False


def propose_patch(
    artifact: CapabilityArtifact,
    cand: DriftCandidate,
    *,
    store: Any,
    run_id: str,
    router: Any | None = None,  # reserved: post-run classification, out of the replay loop
) -> CapabilityArtifact | None:
    """Build + persist a v+1 DRAFT that adds a candidate rule for `cand`.

    Returns the saved draft, or None when there is nothing useful to propose
    (no distinctive text, or the state is already covered by an existing rule,
    or a drift draft for this capability already carries the same phrases).
    """
    phrases = [p for p in (cand.observed_phrases or []) if p.strip()][:4]
    if not phrases:
        return None
    if _covered(artifact, phrases):
        return None

    # Don't stack drift drafts: if the newest version is already an un-reviewed
    # drift draft, patch that one instead of spawning another.
    latest = None
    try:
        latest = store.latest(artifact.name, artifact.vendor_app_id)
    except Exception:  # noqa: BLE001
        latest = None
    reuse = (
        latest is not None
        and latest.status == ArtifactStatus.DRAFT
        and latest.record_outcome == "drift_patch"
    )
    base = latest if reuse else artifact
    if _covered(base, phrases):
        return None

    rule = BusinessOutcomeRule(
        code=f"unclassified_{_slug(phrases[0])}",
        when=Condition(
            kind="text_present",
            params={"any": phrases},
            description="drift-proposed — REVIEW: is this a business outcome or a recoverable hiccup?",
        ),
        message=phrases[0],
        from_step=max(cand.from_step, 0),
        observed=True,
        observed_run_ids=[run_id],
    )

    patched = base.model_copy(deep=True)
    patched.artifact_id = _uuid().hex
    patched.status = ArtifactStatus.DRAFT
    patched.is_default = False
    patched.duplicate_of = None
    patched.supersedes = artifact.version
    patched.record_outcome = "drift_patch"
    patched.created_from_run_id = run_id
    patched.reviewed_by = None
    patched.reviewed_at = None
    patched.review_notes = (
        f"AUTO-DRAFT from replay {run_id}: replay broke on an unrecognised screen "
        f"at/after step {cand.from_step} ({cand.observed_url}). Expected "
        f"{cand.failed_checkpoint!r}. Proposed a text_present rule over the page's "
        f"own text — set the real code and confirm business-outcome vs recoverable, "
        f"or reject."
    )
    patched.known_outcomes = list(patched.known_outcomes) + [rule]

    if reuse:
        return store.replace_draft(patched, artifact_id=latest.artifact_id, version=latest.version)
    return store.save_draft(patched)  # version -> MAX+1 for (name, vendor_app_id)
