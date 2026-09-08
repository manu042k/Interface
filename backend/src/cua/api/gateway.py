"""Goal / Capability Gateway.

  POST /runs                          start a discovery run (ST-021)
  GET  /runs/{run_id}                 run status + resulting artifact
  GET  /artifacts                     list (filter by status/name/tenant) (ST-025)
  GET  /artifacts/{id}/versions/{v}   full artifact
  POST /artifacts/{id}/versions/{v}/promote   approve|reject (ST-025)
  POST /replays/{artifact_id}/invoke  deterministic replay (ST-030); sync long-poll
  GET  /replays/{invocation_id}       replay result
  GET  /capabilities                  agent-facing catalog of approved capabilities (stretch)
  WS   /ws/runs/{run_id}/events       live event timeline (backlog + stream)
  WS   /ws/runs/{run_id}/terminal     bash into the run's live sandbox container
  GET  /runs/{run_id}/report[.md]     assembled run report (JSON / Markdown)
  GET  /evidence/{run_id}/{path}      evidence blob (screenshots for the report)

Everything behind this is the in-process monolith (ADR-01).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path as FsPath
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
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
    novnc_url: str | None = None
    sandbox_container: str | None = None


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
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
                run_id=invocation_id, idempotency_key=req.idempotency_key, run=run,
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

    # -- live run stream (event timeline) --------------------------
    @app.websocket("/ws/runs/{run_id}/events")
    async def run_events_ws(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        sink = app.state.system.sink
        try:
            # backlog first, then live
            for ev in sink.read_events(run_id):
                await websocket.send_json(ev)
            run = app.state.runs.get(run_id)
            if run and run.status in _TERMINAL_RUN:
                await websocket.close()
                return
            async for ev in sink.subscribe(run_id):
                await websocket.send_json(ev)
                if ev.get("event") == "run_finished":
                    break
        except (WebSocketDisconnect, Exception):  # noqa: BLE001
            pass
        finally:
            with _suppress():
                await websocket.close()

    # -- live sandbox terminal (docker exec) ----------------------
    @app.websocket("/ws/runs/{run_id}/terminal")
    async def run_terminal_ws(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        sys: System = app.state.system
        run = app.state.runs.get(run_id)
        mgr = sys.sandbox_manager
        if run is None or not run.sandbox_container or mgr is None:
            await websocket.send_text("\r\n[no live sandbox for this run]\r\n")
            await websocket.close()
            return
        proc = await mgr.exec_process(run.sandbox_container, ["bash", "-i"])

        async def pump_out() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)

        out_task = asyncio.create_task(pump_out())
        try:
            while True:
                msg = await websocket.receive_text()
                if proc.stdin is not None:
                    proc.stdin.write(msg.encode())
                    await proc.stdin.drain()
        except (WebSocketDisconnect, Exception):  # noqa: BLE001
            pass
        finally:
            out_task.cancel()
            with _suppress():
                proc.kill()
            with _suppress():
                await websocket.close()

    # -- run report ---------------------------------------------
    @app.get("/runs/{run_id}/report")
    async def run_report(run_id: str) -> dict[str, Any]:
        return _build_report(app, run_id)

    @app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
    async def run_report_md(run_id: str) -> str:
        return _report_markdown(_build_report(app, run_id))

    # -- evidence blobs (screenshots for the report page) ---------
    @app.get("/evidence/{run_id}/{path:path}")
    async def evidence_file(run_id: str, path: str) -> FileResponse:
        root = FsPath(app.state.config.evidence_root).resolve()
        target = (root / run_id / path).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            raise HTTPException(404, "no such evidence file")
        return FileResponse(target)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


_TERMINAL_RUN = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.DEAD_END}


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def _build_report(app: FastAPI, run_id: str) -> dict[str, Any]:
    run = app.state.runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"no such run: {run_id}")
    sys: System = app.state.system
    events = sys.sink.read_events(run_id)
    evidence = sorted(
        p.name for p in (FsPath(app.state.config.evidence_root) / run_id).glob("*")
        if p.is_file() and not p.name.endswith(".jsonl") and not p.name.endswith(".meta.json")
    )
    timeline = [
        {k: e.get(k) for k in ("ts", "step", "event", "tool", "reasoning", "verdict",
                               "reason", "action_type", "ok", "matched_strategy",
                               "description", "code", "rule", "recovery", "status")
         if k in e}
        for e in events
        if e.get("event") in {
            "run_started", "decision", "guardrail", "action", "checkpoint",
            "recoverable_condition", "business_outcome", "stuck", "hard_failure",
            "locator_resolution", "intervention_opened", "control_transferred",
            "human_action", "intervention_resolved", "sandbox_started", "run_finished",
        }
    ]
    artifact = None
    if run.artifact_id and run.artifact_version:
        with _suppress():
            artifact = sys.store.get(run.artifact_id, run.artifact_version).model_dump()

    replays = []
    if run.artifact_id:
        for inv_id, res in app.state.replays.items():
            r = app.state.runs.get(inv_id)
            if r and r.artifact_id == run.artifact_id:
                replays.append({"invocation_id": inv_id, "params": r.params, **res.model_dump()})

    return {
        "run": _run_dict(run),
        "generated_at": time.time(),
        "timeline": timeline,
        "artifact": artifact,
        "replays": replays,
        "evidence": [f"/evidence/{run_id}/{name}" for name in evidence],
    }


def _report_markdown(rep: dict[str, Any]) -> str:
    run = rep["run"]
    lines = [
        f"# Run report — {run['run_id']}",
        "",
        f"- **Mode:** {run['mode']}",
        f"- **Goal:** {run.get('goal') or '—'}",
        f"- **Target:** {run['app_target']}",
        f"- **Status:** {run['status']} ({run.get('detail') or ''})",
        f"- **Steps:** {run['step_count']}",
    ]
    if run.get("artifact_id"):
        lines.append(f"- **Artifact:** {run['artifact_id']} v{run['artifact_version']}")
    lines += ["", "## Timeline", ""]
    for e in rep["timeline"]:
        step = f"[{e['step']}] " if e.get("step") is not None else ""
        bits = [f"**{e['event']}**"]
        for k in ("tool", "verdict", "action_type", "code", "rule", "description", "status"):
            if e.get(k):
                bits.append(f"{k}={e[k]}")
        line = f"- {step}{' '.join(bits)}"
        if e.get("reasoning"):
            line += f"\n  - _{e['reasoning']}_"
        lines.append(line)
    if rep.get("replays"):
        lines += ["", "## Replay invocations", ""]
        for r in rep["replays"]:
            lines.append(f"- params={r.get('params')} → **{r['outcome']}**"
                         + (f" `{r['business_outcome_code']}`" if r.get("business_outcome_code") else "")
                         + (f" outputs={r['outputs']}" if r.get("outputs") else ""))
    if rep.get("artifact"):
        a = rep["artifact"]
        lines += ["", "## Capability artifact", "",
                  f"- input_schema: `{json.dumps(a['input_schema'])}`",
                  f"- output_schema: `{json.dumps(a['output_schema'])}`",
                  f"- checkpoint: `{json.dumps(a['checkpoint'])}`",
                  f"- known_outcomes: {[r['code'] for r in a['known_outcomes']]}",
                  "", "### Steps", ""]
        for s in a["steps"]:
            lines.append(f"{s['step_index']}. **{s['action_type']}** — {s['description']} "
                         f"(idempotent={s['idempotent']})")
            for ls in s["locator_spec"]:
                lines.append(f"   - rank {ls['rank']} `{ls['kind']}` — {ls['rationale']}")
    if rep.get("evidence"):
        lines += ["", "## Evidence", ""] + [f"- {e}" for e in rep["evidence"]]
    return "\n".join(lines) + "\n"


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
        "novnc_url": run.novnc_url, "sandbox_container": run.sandbox_container,
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
