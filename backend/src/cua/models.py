"""ST-002: core data-model contracts (TDD §6.4).

Every object here is a Pydantic model, so construction *is* validation — a
component that builds a malformed `Step` fails at the call site, not three layers
later at an API boundary.

Schema-design notes live next to the types they explain; the artifact schema is a
focal point of the evaluation, so the reasoning is deliberately in-tree.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(StrEnum):
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    NAVIGATE = "navigate"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"
    ASSERT_STATE = "assert_state"


class RiskClass(StrEnum):
    SAFE_REVERSIBLE = "safe_reversible"
    RISKY_IRREVERSIBLE = "risky_irreversible"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class RunMode(StrEnum):
    DISCOVERY = "discovery"
    REPLAY = "replay"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    STUCK = "stuck"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_END = "dead_end"


class ReplayOutcome(StrEnum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    RECOVERABLE_THEN_SUCCESS = "recoverable_then_success"
    HARD_FAILURE = "hard_failure"


class InterventionStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# Locators & conditions
# ---------------------------------------------------------------------------


class LocatorStrategy(BaseModel):
    """One entry in a Step's ranked fallback chain (ADR-02).

    `kind` orders roughly by robustness on legacy markup:
      role_name           - accessibility role + accessible name (most portable)
      label               - form control associated with a <label> text
      text                - visible text / link text
      dom_anchor          - CSS/XPath anchored to a *stable* structural landmark
      relative_to_landmark- "the Nth input after the cell containing 'Balance'"
      test_id             - data-testid (rare in legacy apps, but trust it when present)
      screenshot_region   - bounding box + OCR token; last resort, coordinate based
    """

    kind: Literal[
        "role_name", "label", "text", "dom_anchor", "relative_to_landmark", "test_id", "screenshot_region"
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    rank: int = Field(ge=0, description="0 = try first")
    rationale: str = Field(
        description="Why this strategy is (or is not) robust for this element — required by brief §3.2"
    )


class Condition(BaseModel):
    """A checkpoint / assertion — something we verify, never assume."""

    kind: Literal[
        "url_matches",
        "text_present",
        "text_absent",
        "element_present",
        "element_absent",
        "extract_equals",
        "extract_matches",
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


# ---------------------------------------------------------------------------
# Exceptional-state rules (make error handling part of the *contract*, §3.3)
# ---------------------------------------------------------------------------


class BusinessOutcomeRule(BaseModel):
    """A legitimate non-happy answer the caller must be told about, not a crash.

    e.g. "no such member" — `when` matches, replay stops and returns
    `{outcome: business_outcome, code: "member_not_found"}`.
    """

    code: str = Field(description="stable machine code, e.g. 'member_not_found'")
    when: Condition
    message: str = ""
    # If set, this outcome is only meaningful at/after this step index.
    from_step: int = 0


class RecoverableRule(BaseModel):
    """A known runtime hiccup replay should handle deliberately and continue.

    action:
      dismiss  - click `target` (a known interstitial's OK/close control) then retry the step
      wait     - re-poll `settle` up to `timeout_ms` then retry the step
      reload   - reload the page then retry the step
    """

    name: str
    when: Condition
    action: Literal["dismiss", "wait", "reload"]
    target: list[LocatorStrategy] = Field(default_factory=list)
    settle: Condition | None = None
    timeout_ms: int = 5000
    max_attempts: int = 2


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class ValueBinding(BaseModel):
    """Where a step's input value comes from.

    Exactly one of `literal` / `param` is set. A discovered credential-like
    literal is rejected at record time and must become a `param` (ST-024).
    """

    literal: str | None = None
    param: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ValueBinding:
        if (self.literal is None) == (self.param is None):
            raise ValueError("ValueBinding needs exactly one of {literal, param}")
        return self


class OutputBinding(BaseModel):
    field: str = Field(description="key in the artifact's output_schema")
    shape: Literal["string", "number", "currency", "integer", "boolean", "date"] = "string"


class Step(BaseModel):
    step_index: int = Field(ge=0)
    action_type: ActionType
    description: str

    # Ranked fallback chain; not required for pure navigate/wait_for steps.
    locator_spec: list[LocatorStrategy] = Field(default_factory=list)

    value_binding: ValueBinding | None = None
    output_binding: OutputBinding | None = None
    step_checkpoint: Condition | None = None

    # Required — forces the idempotency decision at authoring time (ADR §3.1).
    idempotent: bool
    mutex_key: str | None = None
    risk_class: RiskClass = RiskClass.SAFE_REVERSIBLE

    wait_timeout_ms: int = 15000

    @model_validator(mode="after")
    def _shape_matches_action(self) -> Step:
        a = self.action_type
        if a in {ActionType.TYPE, ActionType.SELECT} and self.value_binding is None:
            raise ValueError(f"{a.value} step requires a value_binding")
        if a == ActionType.EXTRACT and self.output_binding is None:
            raise ValueError("extract step requires an output_binding")
        if a == ActionType.NAVIGATE and self.value_binding is None:
            raise ValueError("navigate step requires a value_binding (the URL or named action)")
        if a == ActionType.ASSERT_STATE and self.step_checkpoint is None:
            raise ValueError("assert_state step requires a step_checkpoint")
        if (
            a in {ActionType.CLICK, ActionType.TYPE, ActionType.SELECT, ActionType.EXTRACT}
            and not self.locator_spec
        ):
            raise ValueError(f"{a.value} step requires at least one locator strategy")
        return self


# ---------------------------------------------------------------------------
# CapabilityArtifact
# ---------------------------------------------------------------------------


class TenantScope(BaseModel):
    """base recording, or a thin override layered on one (ADR-08)."""

    kind: Literal["base", "override"] = "base"
    tenant_id: str | None = None
    overrides_base_version: int | None = None

    @model_validator(mode="after")
    def _override_needs_target(self) -> TenantScope:
        if self.kind == "override" and (self.tenant_id is None or self.overrides_base_version is None):
            raise ValueError("override scope requires tenant_id and overrides_base_version")
        return self


class CapabilityArtifact(BaseModel):
    artifact_id: str = Field(default_factory=_uuid)
    version: int = Field(default=1, ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT

    # Stable, human-meaningful name an agent invokes by (catalog key).
    name: str
    goal_description: str
    # Reference blurb written by the model once, at record time (never in replay).
    # Falls back to goal_description when unset.
    agent_summary: str | None = None

    # Vendor-product identity — the axis multi-tenant reuse keys on.
    vendor_app_id: str = "generic"
    app_version: str = "unknown"
    tenant_scope: TenantScope = Field(default_factory=TenantScope)

    # JSON Schema (draft 2020-12) for params in / data out.
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    steps: list[Step]
    checkpoint: Condition
    known_outcomes: list[BusinessOutcomeRule] = Field(default_factory=list)
    recoverable_rules: list[RecoverableRule] = Field(default_factory=list)

    risk_class: RiskClass = RiskClass.SAFE_REVERSIBLE

    created_from_run_id: str | None = None
    created_at: float = Field(default_factory=_now)
    reviewed_by: str | None = None
    reviewed_at: float | None = None
    review_notes: str | None = None

    # De-dup / stability: a hash of the meaningful flow structure. A discovery
    # run that reproduces this exact flow bumps `confirmations` instead of
    # creating a near-duplicate version; a run that differs creates vN+1 with
    # `supersedes` set.
    flow_fingerprint: str = ""
    supersedes: int | None = None
    confirmations: int = 0
    last_confirmed_at: float | None = None
    # how this row came to be, from the last record() call: new | new_version |
    # reused | updated_draft. Informational.
    record_outcome: str | None = None

    @model_validator(mode="after")
    def _steps_indexed(self) -> CapabilityArtifact:
        for i, step in enumerate(self.steps):
            if step.step_index != i:
                raise ValueError(f"steps must be 0-indexed and contiguous; step {i} has index {step.step_index}")
        return self

    @property
    def is_replayable(self) -> bool:
        return self.status == ArtifactStatus.APPROVED


# ---------------------------------------------------------------------------
# Runtime records
# ---------------------------------------------------------------------------


class RunRecord(BaseModel):
    run_id: str = Field(default_factory=_uuid)
    mode: RunMode
    tenant_id: str = "default"
    app_target: str
    goal: str | None = None

    status: RunStatus = RunStatus.PENDING
    artifact_id: str | None = None
    artifact_version: int | None = None
    params: dict[str, Any] | None = None

    started_at: float = Field(default_factory=_now)
    ended_at: float | None = None
    step_count: int = 0
    detail: str | None = None

    # Live per-run sandbox (set only when CUA_USE_SANDBOX is on).
    novnc_url: str | None = None
    sandbox_container: str | None = None
    cdp_url: str | None = None

    # How the resulting capability was persisted: new | new_version | reused | updated_draft
    record_outcome: str | None = None


class FailureDetail(BaseModel):
    step_index: int
    expected: str
    observed: str
    evidence_refs: list[str] = Field(default_factory=list)


class ReplayResult(BaseModel):
    outcome: ReplayOutcome
    outputs: dict[str, Any] | None = None
    business_outcome_code: str | None = None
    recovered_conditions: list[str] = Field(default_factory=list)
    failure_detail: FailureDetail | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    steps_executed: int = 0
    duration_seconds: float = 0.0


class InterventionRequest(BaseModel):
    intervention_id: str = Field(default_factory=_uuid)
    run_id: str
    tenant_id: str = "default"
    capability_name: str | None = None
    goal: str | None = None
    step_index: int
    reason: str
    opened_at: float = Field(default_factory=_now)

    context: dict[str, Any] = Field(default_factory=dict)  # screenshot_ref, transcript_tail, current_url

    status: InterventionStatus = InterventionStatus.OPEN
    claimed_by: str | None = None
    claimed_at: float | None = None
    resolved_at: float | None = None
    resolution: str | None = None
    human_actions_log: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Perception / action I/O (surface-agnostic contract)
# ---------------------------------------------------------------------------


class SurfaceState(BaseModel):
    """Normalized snapshot the model and the locator engine both reason from."""

    url: str
    title: str = ""
    ax_summary: str = Field(default="", description="compact accessibility-tree outline")
    dom_excerpt: str = Field(default="", description="relevant, trimmed HTML — never a full dump")
    screenshot_ref: str | None = None
    fingerprint: str = Field(default="", description="hash of structural signature, for drift/caching")
    captured_at: float = Field(default_factory=_now)


class ActionResult(BaseModel):
    ok: bool
    action_type: ActionType
    target_description: str = ""
    matched_strategy: str | None = None
    url_after: str = ""
    state_hash_after: str = ""
    timed_out: bool = False
    error: str | None = None
    extracted: Any | None = None


class ToolCall(BaseModel):
    """A single structured decision from the discovery agent (TDD §1.3)."""

    tool: Literal[
        "observe",
        "click",
        "type",
        "select",
        "navigate",
        "wait_for",
        "extract",
        "assert_state",
        "done",
        "stuck",
    ]
    args: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(default="", description="model's stated rationale — logged with the step")
