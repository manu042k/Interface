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

    tools = [e.tool_call.tool for e in transcript.entries if e.tool_call.tool in {
        "click", "type", "select", "navigate", "extract", "assert_state", "wait_for"} and e.action_ok]
    assert [s.action_type.value for s in art.steps] == tools  # 1:1, no observe/failed steps

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
