"""Phase 4 — ST-021: Goal Gateway."""

from __future__ import annotations

import asyncio

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from cua.api.gateway import create_app


@pytest_asyncio.fixture
async def client(offline_config, mockbank):
    app = create_app(offline_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # trigger startup
        await c.get("/healthz")
        c._mockbank = mockbank  # type: ignore[attr-defined]
        yield c
    # shutdown
    sys = app.state.system
    if sys:
        await sys.shutdown()


async def test_start_run_returns_run_id_immediately(client):
    r = await client.post("/runs", json={
        "goal": "look up member 12345 and read their current savings balance",
        "target": f"{client._mockbank}/search",
    })
    assert r.status_code == 202
    body = r.json()
    assert body["run_id"] and body["status"] == "pending"

    # poll to completion
    run_id = body["run_id"]
    for _ in range(100):
        g = await client.get(f"/runs/{run_id}")
        if g.json()["status"] in {"completed", "failed", "stuck", "dead_end"}:
            break
        await asyncio.sleep(0.1)
    assert g.json()["status"] == "completed", g.json()


async def test_typed_target_host_is_auto_allowed(offline_config, mockbank):
    # The site the operator names is the site they want tested — validation
    # registers its host and accepts it, without an allowlist file entry.
    # Egress to any *other* host is still blocked mid-run (see
    # test_phase2_policy::test_allow_target_opens_the_typed_host_only).
    import pytest
    from fastapi import HTTPException

    from cua.api.gateway import _validate_target_or_400
    from cua.assembly import build_system
    from cua.policy.engine import ActionContext

    sys = build_system(offline_config)
    try:
        out = _validate_target_or_400(sys, "default", "https://legacy-console.example/signon")
        assert out.startswith("https://legacy-console.example/")
        assert sys.policy.check(
            ActionContext("default", "navigate", "https://legacy-console.example/menu")
        ).allowed
        with pytest.raises(HTTPException) as ei:
            _validate_target_or_400(sys, "default", "not-a-url")
        assert ei.value.status_code == 422
    finally:
        await sys.shutdown()


async def test_unknown_run_404(client):
    r = await client.get("/runs/nope")
    assert r.status_code == 404


async def test_start_run_rejects_junk_goal_and_target(client):
    # a throwaway goal never reaches the LLM
    r = await client.post("/runs", json={"goal": "asdf", "target": f"{client._mockbank}/search"})
    assert r.status_code == 422 and "goal" in r.text
    # a non-URL target is a clean 422, not a stack trace
    r = await client.post("/runs", json={
        "goal": "look up member 12345 and read their savings balance", "target": "banana",
    })
    assert r.status_code == 422 and "URL" in r.text
    # a bare hostname with no dot is rejected too
    r = await client.post("/runs", json={
        "goal": "look up member 12345 and read their savings balance", "target": "http://foo/bar",
    })
    assert r.status_code == 422


async def test_target_probe_reports_reachability(client):
    good = await client.get("/targets/probe", params={"url": f"{client._mockbank}/search"})
    assert good.json()["ok"] is True and good.json()["status"] == 200
    bad = await client.get("/targets/probe", params={"url": "http://127.0.0.1:9/none"})
    assert bad.json()["ok"] is False and bad.json()["reason"] == "unreachable"
    invalid = await client.get("/targets/probe", params={"url": "banana"})
    assert invalid.json()["ok"] is False and invalid.json()["reason"] == "invalid"


async def test_replay_hard_failure_proposes_a_drift_patch_draft(client):
    """A replay that breaks on an unrecognised screen -> the gateway files a
    v+1 DRAFT rule proposal and says so on the run, without failing louder."""
    from cua.models import RunStatus

    app = client._transport.app  # type: ignore[attr-defined]
    system = app.state.system

    # record + approve a real capability
    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{client._mockbank}/search",
        params={"member_id": "12345"},
    )
    assert run.status == RunStatus.COMPLETED, run.detail
    art = system.record(transcript, name="drift_gw_cap", vendor_app_id="mockbank")
    system.store.promote(art.artifact_id, art.version, "approve", reviewer="alice")

    # corrupt its final checkpoint so replay reaches the end and can't verify
    broken = system.store.get(art.artifact_id, art.version)
    broken.checkpoint = broken.checkpoint.model_copy(
        update={"kind": "text_present", "params": {"text": "xyzzy never on this page"}}
    )
    system.store._connect().execute(  # noqa: SLF001 - test-only surgical edit
        "UPDATE artifacts SET body = ? WHERE artifact_id = ? AND version = ?",
        (broken.model_dump_json(), art.artifact_id, art.version),
    ).connection.commit()

    r = await client.post(
        f"/replays/{art.artifact_id}/invoke",
        json={"params": {"member_id": "12345"}, "version": art.version, "wait_seconds": 30},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "hard_failure"

    # a new DRAFT version was filed
    latest = system.store.latest("drift_gw_cap", "mockbank")
    assert latest.version == art.version + 1
    assert latest.status.value == "draft"
    assert latest.record_outcome == "drift_patch"
    assert latest.known_outcomes and latest.known_outcomes[-1].code.startswith("unclassified_")
    # the approved version is untouched
    assert system.store.latest_approved("drift_gw_cap", "mockbank").version == art.version


async def test_artifact_runs_links_discovery_origin_and_replay_invocations(client):
    """The capability<-run parent/child view: the discovery run that created it
    plus every replay invocation, resolved across the whole version family."""
    from cua.models import RunMode, RunStatus

    app = client._transport.app  # type: ignore[attr-defined]
    system = app.state.system

    run, transcript = await system.orchestrator.run_discovery(
        goal="look up member 12345 and read their current savings balance",
        target=f"{client._mockbank}/search",
        params={"member_id": "12345"},
        run=None,
    )
    assert run.status == RunStatus.COMPLETED, run.detail
    art = system.record(transcript, name="linkage_cap", vendor_app_id="mockbank")
    system.store.promote(art.artifact_id, art.version, "approve", reviewer="alice")
    # register the discovery run in the gateway's run map (real flow does this
    # via POST /runs; here we drive the orchestrator directly)
    run.artifact_id, run.artifact_version = art.artifact_id, art.version
    app.state.runs[run.run_id] = run

    # two replay invocations
    for _ in range(2):
        r = await client.post(
            f"/replays/{art.artifact_id}/invoke",
            json={"params": {"member_id": "12345"}, "version": art.version},
        )
        assert r.status_code == 200, r.text

    got = await client.get(f"/artifacts/{art.artifact_id}/runs")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["capability"]["name"] == "linkage_cap"
    assert body["created_from_run_id"] == run.run_id
    assert body["counts"] == {"origin": 1, "invocations": 2}
    assert body["origin_runs"][0]["run_id"] == run.run_id
    assert body["origin_runs"][0]["mode"] == RunMode.DISCOVERY
    assert all(iv["mode"] == RunMode.REPLAY for iv in body["invocations"])
    assert all(iv["record_outcome"] == "reused" for iv in body["invocations"])

    missing = await client.get("/artifacts/nope/runs")
    assert missing.status_code == 404
