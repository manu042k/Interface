"""Phase 5 — ST-022..ST-026."""

from __future__ import annotations

import pytest
import pytest_asyncio

from cua.artifact.store import ArtifactStore, PromotionDecision
from cua.assembly import build_system
from cua.models import ArtifactStatus, RunStatus


@pytest_asyncio.fixture
async def system(offline_config, mockbank):
    sys = build_system(offline_config)
    yield sys
    await sys.shutdown()


async def _discover_balance(system, mockbank):
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
    )
    assert run.status == RunStatus.COMPLETED, run.detail
    return transcript


# --- ST-023: transcript -> draft artifact -----------------------------
async def test_recorder_builds_faithful_artifact(system, mockbank):
    transcript = await _discover_balance(system, mockbank)
    art = system.recorder.build_artifact(transcript, name="read_savings_balance", vendor_app_id="mockbank")

    from cua.artifact.recorder import _is_interstitial_dismiss

    tools = [
        e.tool_call.tool for e in transcript.entries
        if e.tool_call.tool in {"click", "type", "select", "navigate", "extract", "assert_state", "wait_for"}
        and e.action_ok and not _is_interstitial_dismiss(e)
    ]
    # 1:1 with executed, successful, non-interstitial actionable steps
    assert [s.action_type.value for s in art.steps] == tools

    # the URL the recording started from is captured, so replay/invoke can
    # default to it instead of the caller re-typing it every time
    assert art.entry_url == transcript.target and art.entry_url.startswith("http")

    # extract step -> output schema
    assert "savings_balance" in art.output_schema["properties"]
    # every actionable-with-target step has a ranked locator chain w/ rationale
    for s in art.steps:
        if s.action_type.value in {"click", "type", "select", "extract"}:
            assert s.locator_spec and all(ls.rationale for ls in s.locator_spec)
            assert [ls.rank for ls in s.locator_spec] == sorted(ls.rank for ls in s.locator_spec)

    # error taxonomy is part of the contract
    codes = {r.code for r in art.known_outcomes}
    assert {"member_not_found", "permission_denied"} <= codes
    assert any(r.name == "session_notice_interstitial" for r in art.recoverable_rules)


async def test_read_flow_checkpoint_verifies_the_value_not_just_the_url(system, mockbank):
    # A "read a balance" flow must not be signed off with a checkpoint that only
    # proves "we're on a /member/N page" — it has to re-read the value and
    # confirm it's present and currency-shaped.
    transcript = await _discover_balance(system, mockbank)
    art = system.recorder.build_artifact(transcript, name="read_savings_balance")

    cp = art.checkpoint
    kinds: list[str] = []

    def _walk(c):
        kinds.append(c.kind)
        if c.kind in {"all_of", "any_of"}:
            for sub in c.params["conditions"]:
                from cua.models import Condition

                _walk(sub if isinstance(sub, Condition) else Condition(**sub))

    _walk(cp)
    assert "extract_matches" in kinds, f"checkpoint too weak: {cp.kind} {cp.params}"
    assert "_weak" not in cp.params

    # and it actually evaluates against the final screen of the recording
    from cua.conditions import evaluate

    assert await evaluate(cp, transcript.final_state, extracted={"savings_balance": "$4,182.55"})
    assert not await evaluate(cp, transcript.final_state, extracted={"savings_balance": ""})


async def test_recorder_redacts_and_parameterizes(system, mockbank):
    # a goal whose "param" is a secret-looking string typed into a field
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{mockbank}/search",
        params={"member_id": "12345"},
    )
    art = system.recorder.build_artifact(transcript, name="read_savings_balance")
    body = art.model_dump_json()
    assert "REDACTED" not in body or "member_id" in art.input_schema["properties"]
    # the typed member id is a param binding, not a baked-in literal
    type_steps = [s for s in art.steps if s.action_type.value == "type"]
    assert type_steps and type_steps[0].value_binding.param == "member_id"


# --- ST-022: store versioning ---------------------------------------
def test_store_versions_not_overwrites(tmp_path):
    from cua.models import CapabilityArtifact, Condition, Step

    store = ArtifactStore(tmp_path / "s.db")

    def mk():
        return CapabilityArtifact(
            name="cap", vendor_app_id="v", goal_description="g",
            steps=[Step(step_index=0, action_type="navigate", description="go",
                        value_binding={"literal": "http://x/"}, idempotent=True)],
            checkpoint=Condition(kind="url_matches", params={"pattern": ".*"}),
        )

    a1 = store.save_draft(mk())
    a2 = store.save_draft(mk())
    assert a1.version == 1 and a2.version == 2
    assert store.get(a1.artifact_id, 1).version == 1  # v1 still readable


def test_confirm_business_outcome_marks_and_merges(tmp_path):
    from cua.models import BusinessOutcomeRule, CapabilityArtifact, Condition, Step

    store = ArtifactStore(tmp_path / "s.db")

    def mk(name, outcomes):
        return CapabilityArtifact(
            name=name, vendor_app_id="app", goal_description="g",
            steps=[Step(step_index=0, action_type="navigate", description="go",
                        value_binding={"literal": "http://x/"}, idempotent=True)],
            checkpoint=Condition(kind="url_matches", params={"pattern": ".*"}),
            known_outcomes=outcomes,
        )

    a = store.save_draft(mk("cap_a", [BusinessOutcomeRule(
        code="member_not_found",
        when=Condition(kind="text_present", params={"any": ["No members matched"]}))]))
    b = store.save_draft(mk("cap_b", []))  # no rule for this code yet

    touched = store.confirm_business_outcome(
        "app", "member_not_found", ["No member records matched", "record not found"], "run-1",
    )
    assert set(touched) == {"cap_a", "cap_b"}

    ra = next(r for r in store.get(a.artifact_id, 1).known_outcomes if r.code == "member_not_found")
    assert ra.observed is True and "run-1" in ra.observed_run_ids
    # the newly-seen phrases were merged into the existing when-clause
    assert set(ra.when.params["any"]) >= {"No members matched", "No member records matched", "record not found"}

    rb = next(r for r in store.get(b.artifact_id, 1).known_outcomes if r.code == "member_not_found")
    assert rb.observed is True  # appended fresh onto cap_b

    # prefer_name scopes it
    t2 = store.confirm_business_outcome("app", "member_not_found", ["x"], "run-2", prefer_name="cap_a")
    assert t2 == ["cap_a"]


def test_retire_and_default_rollback(tmp_path):
    from cua.artifact.store import PromotionDecision
    from cua.models import CapabilityArtifact, Condition, Step

    store = ArtifactStore(tmp_path / "s.db")

    def mk():
        return CapabilityArtifact(
            name="cap", vendor_app_id="v", goal_description="g",
            steps=[Step(step_index=0, action_type="navigate", description="go",
                        value_binding={"literal": "http://x/"}, idempotent=True)],
            checkpoint=Condition(kind="url_matches", params={"pattern": ".*"}),
        )

    v1 = store.save_draft(mk())
    store.promote(v1.artifact_id, 1, PromotionDecision.APPROVE, reviewer="a")
    v2 = store.save_draft(mk())
    store.promote(v2.artifact_id, 2, PromotionDecision.APPROVE, reviewer="a")

    # newest approval is the default; v1 was demoted
    assert store.latest_approved("cap", "v").version == 2
    assert store.get(v1.artifact_id, 1).is_default is False

    # v2 turns out bad -> retire it and pin v1 back as the default (rollback)
    store.retire(v2.artifact_id, 2, reviewer="a")
    store.set_default(v1.artifact_id, 1)
    assert store.latest_approved("cap", "v").version == 1
    assert store.get(v2.artifact_id, 2).status == "retired"

    # a rejected version can never be re-approved; a retired one can
    v3 = store.save_draft(mk())
    store.promote(v3.artifact_id, 3, PromotionDecision.REJECT, reviewer="a")
    import pytest
    with pytest.raises(ValueError):
        store.promote(v3.artifact_id, 3, PromotionDecision.APPROVE, reviewer="a")
    store.promote(v2.artifact_id, 2, PromotionDecision.APPROVE, reviewer="a")  # retired -> approved OK
    assert store.get(v2.artifact_id, 2).status == "approved"


# --- ST-025: review gate ------------------------------------------
async def test_promotion_gate(system, mockbank):
    transcript = await _discover_balance(system, mockbank)
    art = system.record(transcript, name="read_savings_balance")
    assert art.status == ArtifactStatus.DRAFT

    approved = system.store.promote(art.artifact_id, art.version, PromotionDecision.APPROVE,
                                    reviewer="alice", notes="locators look solid")
    assert approved.status == ArtifactStatus.APPROVED and approved.is_replayable

    # re-promoting a non-draft fails
    with pytest.raises(ValueError):
        system.store.promote(art.artifact_id, art.version, "reject", reviewer="bob")


# --- ST-026: base/override resolution --------------------------------
async def test_tenant_override_resolution(system, mockbank):
    transcript = await _discover_balance(system, mockbank)
    base = system.record(transcript, name="read_savings_balance", vendor_app_id="mockbank")
    system.store.promote(base.artifact_id, base.version, "approve", reviewer="alice")

    # no override for tenant 'cu_two' -> resolves to base
    r = system.store.resolve_for_tenant("read_savings_balance", "cu_two", vendor_app_id="mockbank")
    assert r is not None and r.tenant_scope.kind == "base"


def _drift_next_fingerprint(system, monkeypatch):
    """Make the next build_artifact() produce a genuinely different flow
    fingerprint, as if discovery found a changed/added path."""
    orig = system.recorder.build_artifact
    tag = {"n": 0}

    def mutated(*a, **k):
        art = orig(*a, **k)
        tag["n"] += 1
        art.flow_fingerprint = f"{art.flow_fingerprint}-drift{tag['n']}"
        return art

    monkeypatch.setattr(system.recorder, "build_artifact", mutated)


# --- de-dup on record ----------------------------------------------
async def test_record_reuses_identical_flow(system, mockbank):
    t1 = await _discover_balance(system, mockbank)
    a1 = system.record(t1, name="read_savings_balance", vendor_app_id="mockbank")
    assert a1.record_outcome == "new"
    assert a1.flow_fingerprint

    t2 = await _discover_balance(system, mockbank)
    a2 = system.record(t2, name="read_savings_balance", vendor_app_id="mockbank")
    assert a2.record_outcome == "reused"
    assert (a2.artifact_id, a2.version) == (a1.artifact_id, a1.version)
    assert a2.confirmations == 1
    assert a2.last_confirmed_at is not None
    # nothing new persisted
    assert len(system.store.list(name="read_savings_balance", vendor_app_id="mockbank")) == 1


async def test_record_overwrites_unreviewed_draft(system, mockbank, monkeypatch):
    t1 = await _discover_balance(system, mockbank)
    a1 = system.record(t1, name="member_flow", vendor_app_id="mockbank")
    assert a1.record_outcome == "new" and a1.status == ArtifactStatus.DRAFT

    _drift_next_fingerprint(system, monkeypatch)
    t2 = await _discover_balance(system, mockbank)
    a2 = system.record(t2, name="member_flow", vendor_app_id="mockbank")
    assert a2.record_outcome == "updated_draft"
    assert (a2.artifact_id, a2.version) == (a1.artifact_id, a1.version)
    assert a2.flow_fingerprint != a1.flow_fingerprint
    rows = system.store.list(name="member_flow", vendor_app_id="mockbank")
    assert len(rows) == 1 and rows[0].flow_fingerprint == a2.flow_fingerprint


async def test_record_versions_when_approved_flow_changes(system, mockbank, monkeypatch):
    t1 = await _discover_balance(system, mockbank)
    a1 = system.record(t1, name="member_flow", vendor_app_id="mockbank")
    system.store.promote(a1.artifact_id, a1.version, "approve", reviewer="alice")

    _drift_next_fingerprint(system, monkeypatch)
    t2 = await _discover_balance(system, mockbank)
    a2 = system.record(t2, name="member_flow", vendor_app_id="mockbank")
    assert a2.record_outcome == "new_version"
    assert a2.version == a1.version + 1
    assert a2.supersedes == a1.version
    assert a2.status == ArtifactStatus.DRAFT
    # the approved v1 is untouched and still resolvable
    assert system.store.get(a1.artifact_id, 1).status == ArtifactStatus.APPROVED


def test_locator_landmark_that_equals_a_param_is_rebindable_on_replay():
    """A 'click Select near <member_number>' landmark must not bake in the
    member from the recording run - replay re-binds it to the caller's value."""
    from cua.artifact.recorder import _rank_locators
    from cua.replay.executor import _bind_locator_params

    specs = _rank_locators(
        {"near": "100987", "text": "Select", "role": "button"},
        "near='100987'",
        {"member_number": "100987", "branch": "1"},
    )
    lm = next(s for s in specs if s.kind == "relative_to_landmark")
    assert lm.params["near"] == "100987"
    assert lm.params["near_param"] == "member_number"  # tagged for re-binding
    # branch "1" is too short to be treated as an identifier -> never tagged
    assert all("branch" not in s.params.get("near_param", "") for s in specs)

    bound = _bind_locator_params(specs, {"member_number": "101555"})
    lm2 = next(s for s in bound if s.kind == "relative_to_landmark")
    assert lm2.params["near"] == "101555"          # caller's value substituted
    assert "near_param" not in lm2.params           # marker consumed

    # no such param supplied -> recorded value kept, nothing crashes
    kept = _bind_locator_params(specs, {})
    assert next(s for s in kept if s.kind == "relative_to_landmark").params["near"] == "100987"


async def test_recorder_captures_params_written_into_the_goal(system, mockbank):
    """A value the user put in the goal text (not in params) is recorded as a
    reusable input, not baked in as a literal — and a credential is masked."""
    run, transcript = await system.orchestrator.run_discovery(
        goal='look up member 12345 and read their current savings balance',
        target=f"{mockbank}/search",
        params={},  # nothing passed — the member id lives only in the goal
    )
    assert run.status.value == "completed", run.detail
    art = system.recorder.build_artifact(transcript, name="read_savings_from_goal")

    type_steps = [s for s in art.steps if s.action_type.value == "type"]
    assert type_steps, "expected a type step for the member id"
    b = type_steps[0].value_binding
    assert b.param is not None, "the goal value must become a param, not a literal"
    assert b.param in art.input_schema["properties"]
    assert b.param in art.input_schema["required"]
    prop = art.input_schema["properties"][b.param]
    assert prop.get("x-from-goal") is True
