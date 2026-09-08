"""ST-021: Goal Gateway.

`POST /runs` validates the target against the tenant allowlist at ingress, creates
a Run, and returns `{run_id, status}` immediately — the run executes as a
background task. `GET /runs/{id}` reports status and, once done, the artifact id.

Replay + review + intervention routes are added by later phases; this module owns
the app factory and the shared in-process state.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..assembly import System, build_system
from ..config import Config, load_config
from ..models import ActionType, RunMode, RunRecord, RunStatus
from ..policy.engine import ActionContext, PolicyVerdict


class StartRunRequest(BaseModel):
    goal: str
    target: str
    mode: Literal["discovery"] = "discovery"
    tenant: str = "default"
    params: dict[str, Any] = Field(default_factory=dict)
    confirm_risky: bool = False


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


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Computer-Use Automation System", version="0.1.0")
    app.state.config = config or load_config(strict=False)
    # Composition is cheap (the browser starts lazily on first session), so we
    # build eagerly — works the same under uvicorn and under an ASGI test client.
    app.state.system = build_system(app.state.config)
    app.state.runs: dict[str, RunRecord] = {}
    app.state.transcripts: dict[str, Any] = {}
    app.state.tasks: dict[str, asyncio.Task] = {}

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        sys: System | None = app.state.system
        if sys is not None:
            await sys.shutdown()

    # -- ST-021 ------------------------------------------------------
    @app.post("/runs", response_model=StartRunResponse, status_code=202)
    async def start_run(req: StartRunRequest) -> StartRunResponse:
        sys: System = app.state.system
        _validate_target_or_400(sys, req.tenant, req.target)

        run = RunRecord(mode=RunMode.DISCOVERY, tenant_id=req.tenant, app_target=req.target, goal=req.goal)
        app.state.runs[run.run_id] = run

        async def _execute() -> None:
            try:
                finished, transcript = await sys.orchestrator.run_discovery(
                    goal=req.goal, target=req.target, tenant=req.tenant,
                    params=req.params, run=run, confirm_risky=req.confirm_risky,
                )
                app.state.transcripts[run.run_id] = transcript
                # Phase 5 wires the Artifact Recorder here.
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
        return RunView(
            run_id=run.run_id, mode=run.mode, status=run.status, tenant_id=run.tenant_id,
            app_target=run.app_target, goal=run.goal, detail=run.detail, step_count=run.step_count,
            artifact_id=run.artifact_id, artifact_version=run.artifact_version,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _validate_target_or_400(sys: System, tenant: str, target: str) -> None:
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.hostname:
        raise HTTPException(422, f"target must be an absolute URL, got {target!r}")
    decision = sys.policy.check(
        ActionContext(tenant_id=tenant, action_type=ActionType.NAVIGATE, target_url=target)
    )
    if decision.verdict == PolicyVerdict.BLOCK:
        raise HTTPException(422, f"target not permitted by allowlist for tenant {tenant!r}: {decision.reason}")
