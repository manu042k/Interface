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
