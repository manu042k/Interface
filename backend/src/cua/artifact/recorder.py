"""ST-023/024: Artifact Recorder — successful transcript -> draft CapabilityArtifact.

Design choices that matter here:

* The artifact steps mirror the *executed, successful, actionable* steps 1:1.
  `observe`, failed actions, and guardrail-rejected actions never become steps.
* Every step gets a RANKED locator chain (ADR-02), not one selector. The rank
  order encodes robustness on legacy markup: role+name > label > row-relative >
  visible text > raw dom anchor. Each strategy carries a `rationale` string —
  the "your reasoning about robustness" the brief asks for in §3.2.
* Free-typed values are parameterized: if a typed value equals a supplied param
  it becomes a `param` binding; if it merely looks secret it is refused as a
  literal and recorded as a param the caller must supply (ST-024). Ordinary
  literals (a dropdown option) stay literal.
* The Recorder seeds `known_outcomes` and `recoverable_rules` with the
  exceptional states this flow can plausibly hit, so replay's error taxonomy is
  part of the recorded contract, not bolted on. In a real system these come from
  per-app knowledge + review; here they are derived from the flow shape and
  flagged as review-worthy.
* The whole artifact is passed through `redact()` before it is returned.
"""

from __future__ import annotations

import re
from typing import Any

from ..conditions import shape_pattern as _shape_pattern
from ..discovery.orchestrator import DiscoveryTranscript, TranscriptEntry
from ..models import (
    ActionType,
    BusinessOutcomeRule,
    CapabilityArtifact,
    Condition,
    LocatorStrategy,
    OutputBinding,
    RecoverableRule,
    RiskClass,
    Step,
    ValueBinding,
)
from ..redaction import _SENSITIVE_KEYS, redact_text


def _is_sensitive_key(k: str) -> bool:
    return str(k).strip().lower().replace("-", "_") in _SENSITIVE_KEYS


def flow_fingerprint(a: CapabilityArtifact) -> str:
    """A hash of the *meaningful* flow structure — used to tell a re-run that
    reproduced an existing capability from one that genuinely changed.

    Included: ordered (action, top-locator kind + normalized params, param name /
    output field), the checkpoint kind+params, and the set of known-outcome
    codes. Excluded: step descriptions, locator rationale, literal values that
    are parameters, timestamps, review state.
    """
    import hashlib
    import json

    parts: list[Any] = []
    for s in a.steps:
        loc = s.locator_spec[0] if s.locator_spec else None
        loc_sig = None
        if loc is not None:
            p = {k: v for k, v in loc.params.items() if not k.startswith("_")}
            loc_sig = [loc.kind, sorted((k, str(v)) for k, v in p.items())]
        bind = None
        if s.value_binding and s.value_binding.param:
            bind = f"param:{s.value_binding.param}"
        elif s.value_binding and s.value_binding.literal is not None:
            bind = f"literal:{s.value_binding.literal}"
        parts.append([
            s.action_type.value,
            loc_sig,
            bind,
            s.output_binding.field if s.output_binding else None,
            (s.step_checkpoint.kind if s.step_checkpoint else None),
        ])
    payload = {
        "steps": parts,
        "checkpoint": [a.checkpoint.kind, sorted((k, str(v)) for k, v in a.checkpoint.params.items())],
        "outcomes": sorted(r.code for r in a.known_outcomes),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


_ACTIONABLE = {
    "click", "type", "select", "navigate", "wait_for",
    "extract", "assert_state", "scroll", "press_key",
}
_RISKY_URL_RE = re.compile(r"/(create|submit|confirm|delete|remove|transfer|post|approve)(/|$|\?)", re.I)
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-]{0,20}")  # looks like a name/id attr, not a label
_SECRETISH_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")  # mixed alnum, 8+ — conservative
# Interstitials are runtime-variable: they belong in recoverable_rules, not the
# linear step list. A click made *while one was on screen* is dropped from steps
# and covered by a recoverable rule instead.
_INTERSTITIAL_MARKERS = ("session notice", "your session will expire", "please acknowledge")


class ArtifactRecorder:
    def build_artifact(
        self,
        transcript: DiscoveryTranscript,
        *,
        name: str,
        vendor_app_id: str = "generic",
        app_version: str = "unknown",
    ) -> CapabilityArtifact:
        steps: list[Step] = []
        output_props: dict[str, Any] = {}
        param_props: dict[str, Any] = {}
        forced_params: dict[str, str] = {}
        idx = 0

        actionable = [
            e for e in transcript.entries
            if e.tool_call.tool in _ACTIONABLE and e.action_ok and not _is_interstitial_dismiss(e)
        ]
        actionable = _dedupe_consecutive(actionable)

        for entry in actionable:
            step = self._entry_to_step(entry, idx, transcript.params, param_props, forced_params)
            if entry.tool_call.tool == "extract" and step.output_binding:
                shape = step.output_binding.shape
                output_props[step.output_binding.field] = {"type": _json_type(shape), "x-shape": shape}
            steps.append(step)
            idx += 1

        # inputs: supplied params + any forced-to-param typed values
        for k, v in transcript.params.items():
            if _is_sensitive_key(k):
                # a credential param — declare it, never echo its value as an example
                param_props.setdefault(k, {"type": "string", "x-sensitive": True})
            else:
                param_props.setdefault(k, {"type": "string", "example": redact_text(str(v))[0]})
        for k in forced_params:
            param_props.setdefault(k, {"type": "string", "x-sensitive": True})

        checkpoint = self._derive_checkpoint(transcript, actionable)
        risk = (
            RiskClass.RISKY_IRREVERSIBLE
            if any(s.risk_class == RiskClass.RISKY_IRREVERSIBLE for s in steps)
            else RiskClass.SAFE_REVERSIBLE
        )

        artifact = CapabilityArtifact(
            name=name,
            goal_description=transcript.goal,
            entry_url=transcript.target,
            vendor_app_id=vendor_app_id,
            app_version=app_version,
            input_schema={"type": "object", "properties": param_props, "required": list(transcript.params)},
            output_schema={"type": "object", "properties": output_props, "required": list(output_props)},
            steps=steps,
            checkpoint=checkpoint,
            known_outcomes=self._seed_business_outcomes(transcript),
            recoverable_rules=self._seed_recoverables(transcript),
            risk_class=risk,
            created_from_run_id=transcript.run_id,
        )
        artifact.flow_fingerprint = flow_fingerprint(artifact)
        # ST-024: redact the whole thing before it leaves the recorder.
        return CapabilityArtifact.model_validate(_redact_model(artifact.model_dump()))

    # -- step construction ------------------------------------------
    def _entry_to_step(
        self,
        entry: TranscriptEntry,
        idx: int,
        params: dict[str, Any],
        param_props: dict[str, Any],
        forced_params: dict[str, str],
    ) -> Step:
        tool = entry.tool_call.tool
        args = entry.tool_call.args
        action_type = ActionType(tool)
        desc = entry.tool_call.reasoning or f"{tool} step"

        locator_spec: list[LocatorStrategy] = []
        value_binding: ValueBinding | None = None
        output_binding: OutputBinding | None = None
        step_checkpoint: Condition | None = None

        if tool in {"click", "type", "select", "extract"}:
            locator_spec = _rank_locators(args.get("target"), entry.action_result.get("matched_strategy"))

        if tool == "type":
            raw = str(args.get("value", ""))
            value_binding = _bind_value(raw, params, forced_params, field_hint=_target_hint(args.get("target")))
        elif tool == "select":
            value_binding = ValueBinding(literal=str(args.get("option", "")))
        elif tool == "navigate":
            url = str(args.get("url", ""))
            value_binding = _bind_url(url, params)
        elif tool == "press_key":
            value_binding = ValueBinding(literal=str(args.get("key", "Enter")))
        elif tool == "scroll":
            value_binding = ValueBinding(literal=str(args.get("to_text") or args.get("direction", "down")))

        if tool == "extract":
            field = args.get("as") or "value"
            output_binding = OutputBinding(field=field, shape=args.get("expected_shape", "string"))

        if tool == "assert_state":
            step_checkpoint = _condition_from_arg(args.get("condition"))

        idempotent = _is_idempotent(tool, entry)
        risk_class = RiskClass(entry.action_result.get("risk_class", RiskClass.SAFE_REVERSIBLE))
        mutex_key = _mutex_key(params) if not idempotent else None

        return Step(
            step_index=idx,
            action_type=action_type,
            description=desc,
            locator_spec=locator_spec,
            value_binding=value_binding,
            output_binding=output_binding,
            step_checkpoint=step_checkpoint,
            idempotent=idempotent,
            mutex_key=mutex_key,
            risk_class=risk_class,
        )

    # -- checkpoint / outcomes ------------------------------------
    def _derive_checkpoint(self, transcript: DiscoveryTranscript, actionable: list[TranscriptEntry]) -> Condition:
        """Synthesize the checkpoint that replay will treat as "goal achieved".

        A bare `url_matches` on the final URL only proves "we landed on a
        /member/N page" — it says nothing about whether the read returned a
        value or the update actually took. So we combine every signal the flow
        gives us:
          * the last successful `assert_state` condition (stabilized), if any —
            the model's own success check;
          * for a flow that ends by reading a value, an `extract_matches` on that
            field so replay re-reads it and confirms it is present and
            shaped-right (not empty, not an error string);
          * the final URL as a weak anchor.
        Two or more signals are AND-ed into an `all_of`. A checkpoint that comes
        out as URL-only is tagged `_weak` so review surfaces it.
        """
        parts: list[Condition] = []

        for entry in reversed(actionable):
            if entry.tool_call.tool == "assert_state":
                parts.append(
                    _stabilize_checkpoint(_condition_from_arg(entry.tool_call.args.get("condition")))
                )
                break

        extract_field = _last_extract_field(actionable)
        if extract_field is not None:
            field, shape = extract_field
            parts.append(Condition(
                kind="extract_matches",
                params={"field": field, "pattern": _shape_pattern(shape)},
                description=f"replay re-read '{field}' and it is present and {shape}-shaped",
            ))

        st = transcript.final_state
        url_cond: Condition | None = None
        if st is not None:
            base = re.sub(r"\d+", r"\\d+", re.escape(st.url.split("?")[0]))
            url_cond = Condition(kind="url_matches", params={"pattern": base}, description=f"ended on {st.url}")

        if len(parts) >= 2:
            return Condition(
                kind="all_of",
                params={"conditions": [p.model_dump() for p in parts]},
                description=" AND ".join(p.description or p.kind for p in parts),
            )
        if len(parts) == 1:
            if url_cond is not None and parts[0].kind != "url_matches":
                return Condition(
                    kind="all_of",
                    params={"conditions": [parts[0].model_dump(), url_cond.model_dump()]},
                    description=f"{parts[0].description or parts[0].kind} AND {url_cond.description}",
                )
            return parts[0]
        if url_cond is not None:
            url_cond.params["_weak"] = True
            url_cond.description += " — URL-only checkpoint, does not verify the goal; strengthen before approval"
            return url_cond
        return Condition(kind="text_present", params={"text": ""}, description="no checkpoint derived — review needed")

    def _seed_business_outcomes(self, transcript: DiscoveryTranscript) -> list[BusinessOutcomeRule]:
        g = transcript.goal.lower()
        out: list[BusinessOutcomeRule] = []
        if any(w in g for w in ("look up", "member", "search", "find")):
            out.append(BusinessOutcomeRule(
                code="member_not_found",
                when=Condition(kind="text_present", params={"any": ["No members matched", "was not found"]}),
                message="The requested member does not exist.",
                from_step=1,
            ))
            out.append(BusinessOutcomeRule(
                code="permission_denied",
                when=Condition(kind="text_present", params={"any": ["do not have permission", "member record is restricted"]}),
                message="Caller is not permitted to view this record.",
                from_step=1,
            ))
        if "sub-account" in g or "sub account" in g:
            out.append(BusinessOutcomeRule(
                code="validation_error",
                when=Condition(kind="text_present", params={"any": ["Please choose an account type", "is required"]}),
                message="The form was rejected by server-side validation.",
                from_step=1,
            ))
        return out

    def _seed_recoverables(self, transcript: DiscoveryTranscript) -> list[RecoverableRule]:
        # Recoverable rules describe KNOWN app behaviour, not what happened this
        # run — in production they come from a per-vendor-app rule library curated
        # during review. Seeded here from the flow shape + app family.
        rules: list[RecoverableRule] = [
            RecoverableRule(
                name="session_notice_interstitial",
                when=Condition(kind="text_present", params={"text": "Session Notice"}),
                action="dismiss",
                target=[LocatorStrategy(
                    kind="text", params={"text": "Acknowledge and continue"}, rank=0,
                    rationale="the interstitial's only continue affordance; stable literal label",
                )],
                settle=Condition(kind="text_absent", params={"text": "Session Notice"}),
            )
        ]
        # transient slow load is generic
        rules.append(RecoverableRule(
            name="transient_slow_load",
            when=Condition(kind="text_present", params={"any": ["Loading", "please wait"]}),
            action="wait",
            settle=Condition(kind="text_absent", params={"any": ["Loading", "please wait"]}),
            timeout_ms=8000,
        ))
        # an outright app error is usually transient - reload once and retry
        rules.append(RecoverableRule(
            name="transient_server_error",
            when=Condition(kind="text_present", params={"any": [
                "unexpected error", "please retry", "try again later",
                "temporarily unavailable", "Internal Server Error",
            ]}),
            action="reload",
            settle=Condition(kind="text_absent", params={"any": [
                "unexpected error", "please retry", "Internal Server Error",
            ]}),
            max_attempts=2,
        ))
        return rules


# ---------------------------------------------------------------------------
# locator ranking — the robustness story
# ---------------------------------------------------------------------------


def _rank_locators(target: Any, matched: str | None) -> list[LocatorStrategy]:
    if target is None:
        return []
    if isinstance(target, str):
        target = {"text": target}
    if not isinstance(target, dict):
        return []

    cands: list[LocatorStrategy] = []
    role, name = target.get("role"), target.get("name")
    # The model often puts the accessible name in `text` instead of `name`, and
    # the discovery resolver already treats either as the a11y name
    # (`acc_name = name or text`). If we don't mirror that here, rank 0 becomes a
    # bare `role_name {role}` that matches every link on the page at replay.
    # Ground truth wins: if the adapter reported it resolved by `role=X name='Y'`,
    # use that Y.
    if not name and isinstance(target.get("text"), str):
        name = target["text"]
    if isinstance(matched, str):
        m = re.match(r"role=\S+\s+name=['\"](.+?)['\"]", matched)
        if m:
            name = m.group(1)
    # A model often passes a form-control `name`/`id` token as `name` — not an
    # accessible label. Detect that and emit a name-attr strategy + a role-only
    # fallback, rather than a role_name that won't resolve.
    name_is_token = bool(name) and bool(_TOKEN_RE.fullmatch(str(name)))
    if role and name and not name_is_token:
        cands.append(LocatorStrategy(
            kind="role_name", params={"role": role, "name": name}, rank=0,
            rationale="ARIA role + accessible name: the most portable identifier, survives markup/id churn and works on desktop AX trees too",
        ))
        cands.append(LocatorStrategy(
            kind="text", params={"text": name}, rank=len(cands),
            rationale="the same label as visible text — a fallback if the accessibility name is computed differently at replay",
        ))
    if name_is_token:
        cands.append(LocatorStrategy(
            kind="dom_anchor", params={"css": f'[name="{name}"]'}, rank=len(cands),
            rationale="form-control name attribute — legacy server-rendered forms expose these and they are stable across releases",
        ))
    if role:
        cands.append(LocatorStrategy(
            kind="role_name", params={"role": role}, rank=len(cands),
            rationale="ARIA role only — usable when the control is the sole one of its role on the screen; verify uniqueness at replay",
        ))
    if target.get("label"):
        cands.append(LocatorStrategy(
            kind="label", params={"label": target["label"]}, rank=len(cands),
            rationale="form control bound to its <label> text — stable in server-rendered legacy forms that lack ids",
        ))
    if target.get("placeholder"):
        cands.append(LocatorStrategy(
            kind="label", params={"placeholder": target["placeholder"]}, rank=len(cands),
            rationale="placeholder text — weaker than a label (often absent / localized) but better than positional",
        ))
    if target.get("near"):
        cands.append(LocatorStrategy(
            kind="relative_to_landmark", params={"near": target["near"]}, rank=len(cands),
            rationale="anchored to a visible row label ('the value cell in the Savings row') — robust against table nesting with no ids",
        ))
    if target.get("text"):
        cands.append(LocatorStrategy(
            kind="text", params={"text": target["text"]}, rank=len(cands),
            rationale="visible/link text — readable and fairly stable, but breaks on wording or localization changes",
        ))
    if target.get("css"):
        cands.append(LocatorStrategy(
            kind="dom_anchor", params={"css": target["css"]}, rank=len(cands),
            rationale="CSS path — last-resort fallback; brittle on legacy markup, kept only so replay has something to try",
        ))
    # ensure at least one strategy
    if not cands:
        cands.append(LocatorStrategy(
            kind="text", params=target, rank=0,
            rationale="raw target description carried through — review and strengthen before approval",
        ))
    if matched:
        cands[0].params.setdefault("_discovery_matched", matched)
    return cands


def _dedupe_consecutive(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """A discovery model often repeats an idempotent read/observe-style action
    several times (re-checking a value). Collapse consecutive entries with the
    same tool + target + binding so the artifact has one step, not seven."""
    out: list[TranscriptEntry] = []
    for e in entries:
        if out:
            p, c = out[-1].tool_call, e.tool_call
            same = (
                p.tool == c.tool
                and p.tool in {"extract", "assert_state", "wait_for", "observe", "select", "type"}
                and p.args.get("target") == c.args.get("target")
                and p.args.get("as") == c.args.get("as")
                and p.args.get("condition") == c.args.get("condition")
                and p.args.get("option") == c.args.get("option")
                and p.args.get("value") == c.args.get("value")
            )
            if same:
                continue
        out.append(e)
    return out


def _is_interstitial_dismiss(entry: TranscriptEntry) -> bool:
    if entry.tool_call.tool != "click":
        return False
    st = entry.state_before
    blob = f"{st.title}\n{st.ax_summary}\n{st.dom_excerpt}".lower() if st else ""
    return any(m in blob for m in _INTERSTITIAL_MARKERS)


def _target_hint(target: Any) -> str:
    if isinstance(target, dict):
        return str(target.get("label") or target.get("name") or target.get("placeholder") or target.get("near") or "value")
    return "value"


def _bind_value(raw: str, params: dict[str, Any], forced: dict[str, str], *, field_hint: str) -> ValueBinding:
    for pk, pv in params.items():
        if str(pv) == raw:
            return ValueBinding(param=pk)
    if _SECRETISH_RE.match(raw) and not raw.replace(",", "").replace(".", "").isdigit():
        key = re.sub(r"\W+", "_", field_hint).strip("_").lower() or "secret_value"
        forced[key] = raw
        return ValueBinding(param=key)
    safe, _ = redact_text(raw)
    return ValueBinding(literal=safe)


def _bind_url(url: str, params: dict[str, Any]) -> ValueBinding:
    # canonicalize concrete ids that came from a param into :placeholders
    templ = url
    for pk, pv in params.items():
        if pv and str(pv) in templ:
            templ = templ.replace(str(pv), "{" + pk + "}")
    return ValueBinding(literal=templ)


_DYNAMIC_TOKEN = re.compile(
    r"\b("
    r"[A-Z]{1,5}[-–]\d{3,}"               # ref numbers: SA-736851, CONF-12
    r"|\$[\d,]+\.\d{2}"                     # currency: $4,182.55
    r"|\d{1,2}/\d{1,2}/\d{2,4}"            # dates 9/9/2026
    r"|\d{4}-\d{2}-\d{2}"                  # dates 2026-09-09
    r"|\d{2}:\d{2}(:\d{2})?"              # clock times
    r"|\d{4,}"                             # long bare numbers (ids, amounts)
    r")\b"
)


def _stabilize_checkpoint(cond: Condition) -> Condition:
    """A `text_present` checkpoint whose text carries a per-invocation value (a
    confirmation number, an amount, a date, an id) would only ever match the one
    run it was recorded on. Strip those tokens so replay with other inputs still
    verifies the success screen by its stable wording."""
    if cond.kind != "text_present":
        return cond
    txt = cond.params.get("text")
    if not isinstance(txt, str) or len(txt) < 4:
        return cond
    stripped = re.sub(r"\s{2,}", " ", _DYNAMIC_TOKEN.sub("", txt)).strip(" :–-,.")
    if stripped and stripped != txt and len(stripped) >= 3:
        return Condition(
            kind="text_present",
            params={**cond.params, "text": stripped},
            description=(cond.description or "") + " (dynamic values removed)",
        )
    return cond


def _condition_from_arg(cond: Any) -> Condition:
    if isinstance(cond, dict) and cond.get("kind"):
        return Condition(kind=cond["kind"], params=cond.get("params", {}), description=cond.get("description", ""))
    if isinstance(cond, dict):
        return Condition(kind="text_present", params=cond, description="derived")
    return Condition(kind="text_present", params={"text": str(cond)}, description="derived")


def _is_idempotent(tool: str, entry: TranscriptEntry) -> bool:
    if tool in {"navigate", "wait_for", "extract", "assert_state", "scroll", "press_key"}:
        return True
    url_after = str(entry.action_result.get("url_after", ""))
    if tool == "click" and _RISKY_URL_RE.search(url_after):
        return False  # a submit/create/confirm click — non-idempotent unless proven otherwise
    if tool == "click" and entry.action_result.get("risk_class") == RiskClass.RISKY_IRREVERSIBLE:
        return False
    return True


def _mutex_key(params: dict[str, Any]) -> str | None:
    for k in ("member_id", "account_id", "record_id", "id"):
        if k in params:
            return k
    return None


def _last_extract_field(actionable: list[TranscriptEntry]) -> tuple[str, str] | None:
    """The (field, shape) of the flow's final successful `extract`, if any —
    the value the goal is asking to be read."""
    for entry in reversed(actionable):
        if entry.tool_call.tool == "extract":
            args = entry.tool_call.args
            return (args.get("as") or "value", args.get("expected_shape", "string"))
    return None


def _json_type(shape: str) -> str:
    return {
        "number": "number", "currency": "object", "integer": "integer",
        "boolean": "boolean", "date": "string", "string": "string",
    }.get(shape, "string")


def _redact_model(data: Any) -> Any:
    from ..redaction import redact

    return redact(data)
