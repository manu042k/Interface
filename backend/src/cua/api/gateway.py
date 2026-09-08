"""Goal / Capability Gateway.

  POST /runs                          start a discovery run (ST-021)
  GET  /runs/{run_id}                 run status + resulting artifact
  GET  /artifacts                     list (filter by status/name/tenant) (ST-025)
  GET  /artifacts/{id}/versions/{v}   full artifact
  POST /artifacts/{id}/versions/{v}/promote   approve|reject (ST-025)
  POST /replays/{artifact_id}/invoke  deterministic replay (ST-030); sync long-poll
  GET  /replays/{invocation_id}       replay result
  GET  /capabilities                  agent-facing catalog of approved capabilities (stretch)

Everything behind this is the in-process monolith (ADR-01).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..artifact.store import PromotionDecision
from ..assembly import System, build_system
from ..config import Config, load_config
from ..models import ActionType, ArtifactStatus, RunMode, RunRecord, RunStatus
from ..policy.engine import ActionContext, PolicyVerdict


class StartRunRequest(BaseModel):
    goal: str
    target: str
    mode: Literal["discovery"] = "discovery"
    tenant: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)
    confirm_risky: bool = False
    capability_name: str | None = Field(default=None, description="name to record the resulting artifact under")
    vendor_app_id: str = "mockbank"
    app_version: str = "7.2"


class StartRunResponse(BaseModel):
    run_id: str
    status: str


class RunView(BaseModel):
    run_id: str
    mode: str
    status: str
    tenant_id: str
    app_target: str
    goal: str | None
    detail: str | None
    step_count: int
    artifact_id: str | None = None
    artifact_version: int | None = None


class PromoteRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str
    notes: str | None = None


class InvokeRequest(BaseModel):
    version: int
    params: dict[str, Any] = Field(default_factory=dict)
    target: str
    tenant: str = "default"
    idempotency_key: str | None = None
    wait_seconds: float = Field(default=30.0, ge=0, le=120)


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Computer-Use Automation System", version="0.1.0")
    app.state.config = config or load_config(strict=False)
    app.state.system = build_system(app.state.config)
    app.state.runs: dict[str, RunRecord] = {}
    app.state.transcripts: dict[str, Any] = {}
    app.state.tasks: dict[str, asyncio.Task] = {}
    app.state.replays: dict[str, Any] = {}

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        sys: System | None = app.state.system
        if sys is not None:
            await sys.shutdown()

    # -- ST-021: discovery ------------------------------------------
    @app.post("/runs", response_model=StartRunResponse, status_code=202)
    async def start_run(req: StartRunRequest) -> StartRunResponse:
        sys: System = app.state.system
        _validate_target_or_400(sys, req.tenant, req.target)

        run = RunRecord(mode=RunMode.DISCOVERY, tenant_id=req.tenant, app_target=req.target, goal=req.goal)
        app.state.runs[run.run_id] = run

        async def _execute() -> None:
            try:
                _finished, transcript = await sys.orchestrator.run_discovery(
                    goal=req.goal, target=req.target, tenant=req.tenant,
                    params=req.params, run=run, confirm_risky=req.confirm_risky,
                )
                app.state.transcripts[run.run_id] = transcript
                if run.status == RunStatus.COMPLETED:
                    name = req.capability_name or _slug(req.goal)
                    artifact = sys.record(
                        transcript, name=name, vendor_app_id=req.vendor_app_id, app_version=req.app_version
                    )
                    run.artifact_id = artifact.artifact_id
                    run.artifact_version = artifact.version
            except Exception as exc:  # noqa: BLE001
                run.status = RunStatus.FAILED
                run.detail = f"orchestrator crashed: {exc}"

        app.state.tasks[run.run_id] = asyncio.create_task(_execute())
        return StartRunResponse(run_id=run.run_id, status=run.status)

    @app.get("/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str) -> RunView:
        run = app.state.runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"no such run: {run_id}")
        return RunView(**_run_dict(run))

    # -- ST-025: artifact review -------------------------------------
    @app.get("/artifacts")
    async def list_artifacts(
        status: str | None = None, name: str | None = None, tenant: str | None = None
    ) -> list[dict[str, Any]]:
        arts = app.state.system.store.list(status=status, name=name, tenant_id=tenant)
        return [_artifact_summary(a) for a in arts]

    @app.get("/artifacts/{artifact_id}/versions/{version}")
    async def get_artifact(artifact_id: str, version: int) -> dict[str, Any]:
        try:
            return app.state.system.store.get(artifact_id, version).model_dump()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/artifacts/{artifact_id}/versions/{version}/promote")
    async def promote(artifact_id: str, version: int, req: PromoteRequest) -> dict[str, Any]:
        try:
            art = app.state.system.store.promote(
                artifact_id, version, PromotionDecision(req.decision), reviewer=req.reviewer, notes=req.notes
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"artifact_id": art.artifact_id, "version": art.version, "status": art.status}

    # -- ST-030: deterministic replay ------------------------------
    @app.post("/replays/{artifact_id}/invoke")
    async def invoke_replay(artifact_id: str, req: InvokeRequest) -> dict[str, Any]:
        sys: System = app.state.system
        try:
            artifact = sys.store.get(artifact_id, req.version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

        if artifact.status != ArtifactStatus.APPROVED:
            raise HTTPException(409, f"artifact {artifact_id} v{req.version} is {artifact.status}, not approved — not replay-eligible")

        _validate_target_or_400(sys, req.tenant, req.target)

        errs = sys.replay.validate_params(artifact, req.params)
        if errs:
            raise HTTPException(422, {"error": "params do not match input_schema", "detail": errs})

        invocation_id = "inv_" + uuid.uuid4().hex[:12]
        run = RunRecord(
            run_id=invocation_id, mode=RunMode.REPLAY, tenant_id=req.tenant, app_target=req.target,
            artifact_id=artifact_id, artifact_version=req.version, params=req.params,
        )
        app.state.runs[invocation_id] = run

        async def _do() -> None:
            run.status = RunStatus.RUNNING
            result = await sys.replay.execute(
                artifact, req.params, target=req.target, tenant=req.tenant,
                run_id=invocation_id, idempotency_key=req.idempotency_key,
            )
            app.state.replays[invocation_id] = result
            run.status = RunStatus.COMPLETED if result.outcome.value in {"success", "recoverable_then_success", "business_outcome"} else RunStatus.FAILED
            run.detail = result.outcome.value
            run.ended_at = run.ended_at or None

        task = asyncio.create_task(_do())
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=req.wait_seconds)
        except TimeoutError:
            return {"invocation_id": invocation_id, "status": "running", "poll": f"/replays/{invocation_id}"}

        result = app.state.replays[invocation_id]
        return {"invocation_id": invocation_id, **result.model_dump()}

    @app.get("/replays/{invocation_id}")
    async def get_replay(invocation_id: str) -> dict[str, Any]:
        result = app.state.replays.get(invocation_id)
        run = app.state.runs.get(invocation_id)
        if run is None:
            raise HTTPException(404, f"no such invocation: {invocation_id}")
        if result is None:
            return {"invocation_id": invocation_id, "status": run.status}
        return {"invocation_id": invocation_id, "status": run.status, **result.model_dump()}

    # -- stretch: agent-facing capability catalog -----------------
    @app.get("/capabilities")
    async def capabilities() -> list[dict[str, Any]]:
        arts = app.state.system.store.list(status=ArtifactStatus.APPROVED)
        return [
            {
                "name": a.name, "artifact_id": a.artifact_id, "version": a.version,
                "goal": a.goal_description, "vendor_app_id": a.vendor_app_id,
                "input_schema": a.input_schema, "output_schema": a.output_schema,
                "risk_class": a.risk_class, "invoke": f"/replays/{a.artifact_id}/invoke",
            }
            for a in arts
        ]

    # -- ST-037..ST-040: escalation & operator console ---------------
    @app.get("/interventions")
    async def list_interventions(status: str | None = "open") -> list[dict[str, Any]]:
        return app.state.system.console.inbox(status=status or "open")

    @app.get("/interventions/{intervention_id}")
    async def get_intervention(intervention_id: str) -> dict[str, Any]:
        try:
            return app.state.system.console.context(intervention_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/interventions/{intervention_id}/claim")
    async def claim_intervention(intervention_id: str, body: dict[str, str]) -> dict[str, Any]:
        try:
            return app.state.system.console.claim(intervention_id, operator=body["operator"])
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/interventions/{intervention_id}/take-control")
    async def take_control(intervention_id: str, body: dict[str, str]) -> dict[str, Any]:
        try:
            return app.state.system.console.take_control(intervention_id, operator=body["operator"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/interventions/{intervention_id}/actions")
    async def operator_action(intervention_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return await app.state.system.console.perform(
                intervention_id, body["operator"], body["action"]
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/interventions/{intervention_id}/release")
    async def release_control(intervention_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from ..models import Condition

        cp = body.get("goal_checkpoint")
        checkpoint = Condition(**cp) if cp else None
        try:
            return await app.state.system.console.release_control(
                intervention_id, body["operator"], goal_checkpoint=checkpoint
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


# ---------------------------------------------------------------------------


def _validate_target_or_400(sys: System, tenant: str, target: str) -> None:
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.hostname:
        raise HTTPException(422, f"target must be an absolute URL, got {target!r}")
    decision = sys.policy.check(
        ActionContext(tenant_id=tenant, action_type=ActionType.NAVIGATE, target_url=target)
    )
    if decision.verdict == PolicyVerdict.BLOCK:
        raise HTTPException(422, f"target not permitted by allowlist for tenant {tenant!r}: {decision.reason}")


def _run_dict(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id, "mode": run.mode, "status": run.status, "tenant_id": run.tenant_id,
        "app_target": run.app_target, "goal": run.goal, "detail": run.detail, "step_count": run.step_count,
        "artifact_id": run.artifact_id, "artifact_version": run.artifact_version,
    }


def _artifact_summary(a: Any) -> dict[str, Any]:
    return {
        "artifact_id": a.artifact_id, "version": a.version, "name": a.name, "status": a.status,
        "goal": a.goal_description, "vendor_app_id": a.vendor_app_id, "app_version": a.app_version,
        "tenant_scope": a.tenant_scope.model_dump(), "risk_class": a.risk_class,
        "steps": len(a.steps), "known_outcomes": [r.code for r in a.known_outcomes],
    }


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "capability"
