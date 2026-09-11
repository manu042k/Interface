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
_RISKY_URL_RE = re.compile(
    r"/(create|submit|confirm|delete|remove|transfer|post|approve|billpay|bill-?pay|"
    r"payment|wire|withdraw\w*|deposit|disburse\w*|authoriz\w*|open-?account|close|"
    r"hold|stop-?payment|update-?profile)(/|$|\?|\.htm|\.do|\.aspx)",
    re.I,
)
_TOKEN_RE = re.compile(r"[a-z][a-zA-Z0-9_\-.]{0,40}(?<![.\-_])")  # looks like a name/id attr, not a label
# Legacy Struts-style forms (ParaBank et al.) name fields "customer.firstName",
# "customer.address.street" — mixed case, dotted. A plain lowercase-only token
# regex missed these, so `name_is_token` stayed False, no dom_anchor candidate
# was ever generated, and _rank_locators() fell through to its last-resort
# `kind="text", params=target` branch — but target only has a "name" key, not
# "text", so the resolver failed every such field at replay with
# "strategy not applicable to params". Broadening the regex fixes it at the
# source: any name/id-looking token, not just simple lowercase ones.
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
        derived_params: dict[str, str] = {}  # values the user wrote into the goal
        positional_params: dict[str, str] = {}  # a record id selected by position, not named in the goal
        idx = 0

        actionable = [
            e for e in transcript.entries
            if e.tool_call.tool in _ACTIONABLE and e.action_ok and not _is_interstitial_dismiss(e)
        ]
        actionable = _dedupe_consecutive(actionable)

        for entry in actionable:
            step = self._entry_to_step(
                entry, idx, transcript.params, param_props, forced_params,
                derived_params, transcript.goal, positional_params,
            )
            if entry.tool_call.tool == "extract" and step.output_binding:
                shape = step.output_binding.shape
                output_props[step.output_binding.field] = {"type": _json_type(shape), "x-shape": shape}
            steps.append(step)
            idx += 1

        # inputs: supplied params + forced-secret typed values + values the user
        # wrote into the goal instead of passing them
        for k, v in transcript.params.items():
            if _is_sensitive_key(k):
                # a credential param — declare it, never echo its value as an example
                param_props.setdefault(k, {"type": "string", "x-sensitive": True})
            else:
                param_props.setdefault(k, {"type": "string", "example": redact_text(str(v))[0]})
        for k in forced_params:
            param_props.setdefault(k, {"type": "string", "x-sensitive": True})
        # a goal-derived value is recorded as its own `default`, so the
        # capability replays as-is; a caller can still override it. A
        # credential-cued one has no plaintext default and stays required.
        derived_required: list[str] = []
        for k, v in derived_params.items():
            if _is_sensitive_key(k):
                param_props.setdefault(k, {"type": "string", "x-sensitive": True})
                derived_required.append(k)
            else:
                param_props.setdefault(
                    k,
                    {
                        "type": "string",
                        "default": redact_text(str(v))[0],
                        "example": redact_text(str(v))[0],
                        "x-from-goal": True,
                    },
                )

        # a record selected by position (an account/share id) — same recorded
        # value by default (unattended replay is unaffected), but overridable:
        # a caller passing the CURRENT id survives the underlying data changing
        # under a stale literal.
        for k, v in positional_params.items():
            param_props.setdefault(
                k,
                {
                    "type": "string",
                    "default": redact_text(str(v))[0],
                    "example": redact_text(str(v))[0],
                    "x-recorded-default": True,
                },
            )

        required = list(
            dict.fromkeys([*transcript.params, *forced_params, *derived_required])
        )

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
            input_schema={"type": "object", "properties": param_props, "required": required},
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
        derived: dict[str, str],
        goal: str,
        positional: dict[str, str],
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
            locator_spec = _rank_locators(
                args.get("target"),
                entry.action_result.get("matched_strategy"),
                {**params, **derived},  # a landmark may reference a goal-derived value
            )

        if tool == "type":
            raw = str(args.get("value", ""))
            value_binding = _bind_value(
                raw, params, forced_params, derived,
                field_hint=_target_hint(args.get("target")), goal=goal,
            )
        elif tool == "select":
            opt = str(args.get("option", ""))
            # bind to a param when the chosen option is a supplied value (a
            # share id, a branch) so the caller's input actually drives it
            bound = next((pk for pk, pv in params.items() if str(pv) == opt), None)
            if bound:
                value_binding = ValueBinding(param=bound)
            elif opt and _value_in_goal(opt, goal):
                taken = {**{k: str(v) for k, v in params.items()}, **forced_params, **derived}
                key, sensitive = _derive_param_name(
                    opt, _target_hint(args.get("target")), goal, taken
                )
                (forced_params if sensitive else derived)[key] = opt
                value_binding = ValueBinding(param=key)
            elif opt and _looks_like_a_record_selector(_target_hint(args.get("target")), opt):
                # The chosen option (an account/share/record id) is neither a
                # supplied param nor written into the goal — the goal said "the
                # first account" / "the second account", not the literal id. A
                # bare literal here bakes IN the recording session's specific
                # account and hard-fails every replay once that id no longer
                # exists (a real ParaBank case: "select fromAccountId=12345"
                # stops working the moment the account list changes). Capture it
                # as an overridable parameter instead — same recorded value by
                # default, but a caller can pass the CURRENT id.
                taken = {**{k: str(v) for k, v in params.items()}, **forced_params, **derived, **positional}
                key, _sensitive = _derive_param_name(opt, _target_hint(args.get("target")), goal, taken)
                positional[key] = opt
                value_binding = ValueBinding(param=key)
            else:
                value_binding = ValueBinding(literal=opt)
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
            # strip per-run values (a member number, a confirmation id) the same
            # way the final checkpoint is stabilized — otherwise a step
            # checkpoint recorded as "Member No.: 100234" fails every replay
            # with a different member.
            step_checkpoint = _stabilize_checkpoint(_condition_from_arg(args.get("condition")))

        idempotent = _is_idempotent(tool, entry)
        risk_class = RiskClass(entry.action_result.get("risk_class", RiskClass.SAFE_REVERSIBLE))
        # a non-idempotent click that landed on a create/submit/confirm/post/
        # transfer/approve route is an irreversible mutation, whatever the
        # surface adapter guessed — the artifact must carry that so the gateway
        # gates unattended replay on human approval.
        if (
            tool == "click"
            and not idempotent
            and _RISKY_URL_RE.search(str(entry.action_result.get("url_after", "")))
        ):
            risk_class = RiskClass.RISKY_IRREVERSIBLE
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
        from ..outcomes import business_outcomes_for

        g = transcript.goal.lower()
        # per-host library first — a curated entry beats a goal-keyword guess;
        # generic flow-shape seeds fill any code the library doesn't cover.
        out: list[BusinessOutcomeRule] = list(business_outcomes_for(transcript.target))
        have = {r.code for r in out}
        def _add(rule: BusinessOutcomeRule) -> None:
            if rule.code not in have:
                out.append(rule)
                have.add(rule.code)
        if any(w in g for w in ("look up", "member", "search", "find")):
            _add(BusinessOutcomeRule(
                code="member_not_found",
                when=Condition(kind="text_present", params={"any": [
                    "No members matched", "no member records matched", "no records matched",
                    "no matching members", "was not found", "not found", "no results",
                ]}),
                message="The requested member does not exist.",
                from_step=1,
            ))
            _add(BusinessOutcomeRule(
                # the ACTUAL denial wording — not an advisory "restricted /
                # override required" banner that sits on the form for everyone
                # and would false-trigger.
                code="permission_denied",
                when=Condition(kind="text_present", params={"any": [
                    "do not have permission", "member record is restricted",
                    "is not authorized to perform", "not authorized to perform this function",
                    "authorization required", "access denied", "a supervisor must sign on",
                ]}),
                message="The operator is not permitted to perform this action.",
                from_step=1,
            ))
        # any multi-field money/servicing form can be rejected by server-side
        # validation before it posts — a legitimate result the caller must know
        # about, distinct from a crash.
        if any(w in g for w in (
            "sub-account", "sub account", "transfer", "open ", "new share",
            "deposit", "account hold", "place a hold",
        )):
            _add(BusinessOutcomeRule(
                code="validation_error",
                when=Condition(kind="text_present", params={"any": [
                    "could not be validated", "transaction could not be validated",
                    "cannot be debited", "insufficient", "is on hold", "share is HOLD",
                    "Please choose an account type", "is required", "invalid amount",
                    "must be greater than", "not a valid", "already exists", "already taken",
                    "already in use", "not available",
                ]}),
                message="The request was rejected by server-side validation before it posted.",
                from_step=1,
            ))
        return out

    def _seed_recoverables(self, transcript: DiscoveryTranscript) -> list[RecoverableRule]:
        from ..outcomes import recoverables_for

        # Recoverable rules describe KNOWN app behaviour, not what happened this
        # run. Per-host library first (curated), then generic flow-shape seeds
        # for any name the library doesn't already cover.
        rules: list[RecoverableRule] = list(recoverables_for(transcript.target))
        names = {r.name for r in rules}
        def _add(rule: RecoverableRule) -> None:
            if rule.name not in names:
                rules.append(rule)
                names.add(rule.name)

        _add(RecoverableRule(
            name="session_notice_interstitial",
            when=Condition(kind="text_present", params={"text": "Session Notice"}),
            action="dismiss",
            target=[LocatorStrategy(
                kind="text", params={"text": "Acknowledge and continue"}, rank=0,
                rationale="the interstitial's only continue affordance; stable literal label",
            )],
            settle=Condition(kind="text_absent", params={"text": "Session Notice"}),
        ))
        # transient slow load is generic
        _add(RecoverableRule(
            name="transient_slow_load",
            when=Condition(kind="text_present", params={"any": ["Loading", "please wait"]}),
            action="wait",
            settle=Condition(kind="text_absent", params={"any": ["Loading", "please wait"]}),
            timeout_ms=8000,
        ))
        # an outright app error is usually transient - reload once and retry
        _add(RecoverableRule(
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


def _locator_param_for(value: Any, params: dict[str, Any] | None) -> str | None:
    """Return the name of the run param whose value EQUALS this locator string,
    or None. Generic: no value is special-cased — it just asks "did the caller
    supply exactly this string?". When yes, the recorder tags the strategy so
    replay re-binds it per call instead of baking in this run's value (a member
    number, an account id) and only ever working for that one record.

    Guards against a coincidental match: the value must be >=3 chars and not a
    bare number shorter than 4 digits (branch "1", a page "2")."""
    if not params or not isinstance(value, str) or len(value) < 3:
        return None
    if value.isdigit() and len(value) < 4:
        return None
    return next(
        (k for k, v in params.items() if v is not None and str(v) == value and len(str(v)) >= 3),
        None,
    )


def _rank_locators(
    target: Any, matched: str | None, params: dict[str, Any] | None = None
) -> list[LocatorStrategy]:
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
    landmark_from_match: str | None = None
    if isinstance(matched, str):
        m = re.match(r"role=\S+\s+name=['\"](.+?)['\"]", matched)
        if m:
            name = m.group(1)
        # the adapter resolved this control by its label-cell / row-landmark
        # text — that is what actually worked, so record it as rank 0.
        lm = re.match(r"(?:label-cell|near)=['\"](.+?)['\"]", matched)
        if lm:
            landmark_from_match = lm.group(1)
    # A model often passes a form-control `name`/`id` token as `name` — not an
    # accessible label. Detect that and emit a name-attr strategy + a role-only
    # fallback, rather than a role_name that won't resolve.
    name_is_token = bool(name) and bool(_TOKEN_RE.fullmatch(str(name)))
    if landmark_from_match:
        cands.append(LocatorStrategy(
            kind="relative_to_landmark", params={"near": landmark_from_match}, rank=0,
            rationale="the control in the row whose label cell holds this text — how a legacy table form with no <label for> is targeted; survives id/name churn",
        ))
    if role and name and not name_is_token:
        cands.append(LocatorStrategy(
            kind="role_name", params={"role": role, "name": name}, rank=len(cands),
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
        # Defense in depth: a bare `kind="text"` strategy needs a "text" param
        # to be resolvable at replay. If none of the branches above fired but
        # the target still carries a name/id-looking attribute, use that
        # instead of blindly stuffing the whole target dict under "text" —
        # that produced an unresolvable ("strategy not applicable to params")
        # locator whenever `name` wasn't recognised as a token upstream.
        if name:
            cands.append(LocatorStrategy(
                kind="dom_anchor", params={"css": f'[name="{name}"]'}, rank=0,
                rationale="raw target description carried through — review and strengthen before approval",
            ))
        else:
            cands.append(LocatorStrategy(
                kind="text", params=target if target.get("text") else {"text": str(target)}, rank=0,
                rationale="raw target description carried through — review and strengthen before approval",
            ))
    # A strategy value that is really a run param ("Select the row near 100987")
    # gets a `<key>_param` sibling so replay substitutes the caller's value.
    for strat in cands:
        for k in ("near", "text", "name", "label", "placeholder"):
            pk = _locator_param_for(strat.params.get(k), params)
            if pk:
                strat.params[f"{k}_param"] = pk
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


_GENERIC_HINTS = {
    "value", "q", "input", "field", "text", "search", "query", "term", "box", "name",
}
_SECRET_CUE_RE = re.compile(r"(pass\w*|pwd|pin|secret|passcode|otp|token|api[\s_-]?key)\W*$", re.I)


def _value_in_goal(v: str, goal: str) -> bool:
    """The value the model typed appears, verbatim, in what the user asked for —
    a strong signal the user meant it as an INPUT, not a fixed literal."""
    v = v.strip()
    if len(v) < 2 or (v.isdigit() and len(v) < 2):
        return False
    return re.search(r"(?<![\w-])" + re.escape(v) + r"(?![\w-])", goal, re.I) is not None


_RECORD_SELECTOR_HINT_RE = re.compile(
    r"account|acct|share|member|customer|loan|card|profile|payee", re.I
)


def _looks_like_a_record_selector(field_hint: str, value: str) -> bool:
    """A `select` value that names a specific record (an account/share/member
    id) rather than a fixed business choice ("Checking" vs "Savings") — the
    kind of thing that is valid in THIS recording session and stops existing
    the moment the underlying data changes. Recognised by the field's own name
    (an "...AccountId"-shaped select) or by the value itself being a bare
    numeric id with no words (a real business enum reads as text)."""
    v = value.strip()
    if _RECORD_SELECTOR_HINT_RE.search(field_hint or ""):
        return True
    return bool(re.fullmatch(r"\d{3,}", v))


def _derive_param_name(
    value: str, field_hint: str, goal: str, existing: dict[str, str]
) -> tuple[str, bool]:
    """Name a parameter for a value the user wrote into the goal instead of
    passing it. Returns (name, is_sensitive). Reuses an existing name if that
    exact value already has one (so the same value across steps is one param)."""
    for k, v in existing.items():
        if v == value:
            return k, _is_sensitive_key(k)

    # 1) a meaningful form-field identifier ("operator", "amount", "memo")
    hint = re.sub(r"\W+", "_", field_hint or "").strip("_").lower()
    name = hint if (len(hint) >= 3 and hint not in _GENERIC_HINTS and not hint.isdigit()) else ""

    # 2) else the label words right before the value in the goal
    #    "Member Number for 101555" -> member_number ; "as operator teller1" -> operator
    lead = ""
    if not name:
        m = re.search(
            r"([A-Za-z][A-Za-z /_-]{1,28}?)\s*(?:for|:|=|to|is|of|number|no\.?|#|named?)?\s*[\"'“]?"
            + re.escape(value),
            goal,
            re.I,
        )
        if m:
            lead = m.group(1)
            words = re.findall(r"[A-Za-z]+", lead)[-2:]
            name = "_".join(w.lower() for w in words if w.lower() not in {"the", "a", "an", "as"})

    # 3) fallback
    if not name:
        i = 1
        while f"input_{i}" in existing:
            i += 1
        name = f"input_{i}"

    base, i = name, 2
    while name in existing:
        name = f"{base}_{i}"
        i += 1

    before = goal[: goal.lower().find(value.lower())] if value.lower() in goal.lower() else lead
    sensitive = _is_sensitive_key(name) or bool(_SECRET_CUE_RE.search(before))
    return name, sensitive


def _bind_value(
    raw: str,
    params: dict[str, Any],
    forced: dict[str, str],
    derived: dict[str, str],
    *,
    field_hint: str,
    goal: str,
) -> ValueBinding:
    for pk, pv in params.items():
        if str(pv) == raw:
            return ValueBinding(param=pk)
    if _SECRETISH_RE.match(raw) and not raw.replace(",", "").replace(".", "").isdigit():
        key = re.sub(r"\W+", "_", field_hint).strip("_").lower() or "secret_value"
        forced[key] = raw
        return ValueBinding(param=key)
    # the user wrote the value into the goal rather than passing it as a param —
    # capture it as an input so the capability is reusable, not frozen to it.
    if _value_in_goal(raw, goal):
        taken = {**{k: str(v) for k, v in params.items()}, **forced, **derived}
        key, sensitive = _derive_param_name(raw, field_hint, goal, taken)
        (forced if sensitive else derived)[key] = raw
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
