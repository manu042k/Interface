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
from .run_store import RunStore


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
    # Optional success Condition (e.g. {"kind":"text_present","params":{"text":"..."}}).
    # When it holds the run auto-completes - no need for the model to call done.
    success_check: dict[str, Any] | None = None


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
    name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    detail: str | None
    step_count: int
    artifact_id: str | None = None
    artifact_version: int | None = None
    record_outcome: str | None = None
    novnc_url: str | None = None
    sandbox_container: str | None = None
    browser: str = "chromium"
    started_at: float = 0.0
    ended_at: float | None = None
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


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
    app.state.run_store = RunStore(app.state.config.db_path.parent / "runs.json")
    app.state.runs: dict[str, RunRecord] = app.state.run_store.load()
    # a run that was mid-flight when the process last stopped can't resume
    for _r in app.state.runs.values():
        if _r.status in {RunStatus.PENDING, RunStatus.RUNNING}:
            _r.status = RunStatus.FAILED
            _r.detail = _r.detail or "interrupted — server restarted"
    app.state.transcripts: dict[str, Any] = {}
    app.state.tasks: dict[str, asyncio.Task] = {}
    app.state.replays: dict[str, Any] = {}

    def _persist_runs() -> None:
        try:
            app.state.run_store.save_all(app.state.runs)
        except Exception:  # noqa: BLE001 — persistence is best-effort
            pass

    app.state.persist_runs = _persist_runs

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        sys: System | None = app.state.system
        if sys is not None:
            await sys.shutdown()

    # -- ST-021: discovery ------------------------------------------
    @app.post("/runs", response_model=StartRunResponse, status_code=202)
    async def start_run(req: StartRunRequest) -> StartRunResponse:
        sys: System = app.state.system
        target = _validate_target_or_400(sys, req.tenant, req.target)

        run = RunRecord(
            mode=RunMode.DISCOVERY, tenant_id=req.tenant, app_target=target, goal=req.goal,
            name=req.capability_name or _slug(req.goal), params=req.params or None,
            browser="chromium" if sys.sandbox_manager is not None else "chromium (headless)",
        )
        app.state.runs[run.run_id] = run
        app.state.persist_runs()

        async def _execute() -> None:
            try:
                _finished, transcript = await sys.orchestrator.run_discovery(
                    goal=req.goal, target=target, tenant=req.tenant,
                    params=req.params, run=run, confirm_risky=req.confirm_risky,
                    success_check=req.success_check,
                    handoff_wait_s=900.0,  # a stuck run waits for a human, then resumes
                )
                app.state.transcripts[run.run_id] = transcript
                if run.status == RunStatus.COMPLETED:
                    name = req.capability_name or _slug(req.goal)
                    artifact = sys.record(
                        transcript, name=name, vendor_app_id=req.vendor_app_id, app_version=req.app_version
                    )
                    run.artifact_id = artifact.artifact_id
                    run.artifact_version = artifact.version
                    run.record_outcome = artifact.record_outcome
                    if artifact.record_outcome != "reused" and not artifact.agent_summary:
                        await sys.summarize_capability(artifact)  # record-time, best-effort
            except Exception as exc:  # noqa: BLE001
                run.status = RunStatus.FAILED
                run.detail = f"orchestrator crashed: {exc}"
            finally:
                app.state.persist_runs()

        app.state.tasks[run.run_id] = asyncio.create_task(_execute())
        return StartRunResponse(run_id=run.run_id, status=run.status)

    @app.get("/runs")
    async def list_runs(limit: int = 50) -> list[dict[str, Any]]:
        runs = sorted(app.state.runs.values(), key=lambda r: r.started_at, reverse=True)
        return [
            {
                "run_id": r.run_id, "mode": r.mode, "status": r.status,
                "goal": r.goal, "name": r.name,
                "started_at": r.started_at, "ended_at": r.ended_at,
                "step_count": r.step_count, "artifact_id": r.artifact_id,
                "has_sandbox": bool(r.sandbox_container),
            }
            for r in runs[: max(1, min(limit, 200))]
        ]

    @app.get("/runs/active")
    async def active_run() -> dict[str, Any] | None:
        live = [
            r for r in app.state.runs.values()
            if r.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STUCK}
        ]
        if not live:
            return None
        r = max(live, key=lambda x: x.started_at)
        return {"run_id": r.run_id, "status": r.status, "goal": r.goal, "mode": r.mode}

    @app.get("/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str) -> RunView:
        run = app.state.runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"no such run: {run_id}")
        # the frontend polls this while a run is live — cheap way to keep the
        # on-disk copy fresh so progress survives a crash mid-run
        if run.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STUCK}:
            app.state.persist_runs()
        return RunView(**_run_dict(run))

    @app.post("/runs/{run_id}/cancel", response_model=RunView)
    async def cancel_run(run_id: str) -> RunView:
        run = app.state.runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"no such run: {run_id}")
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.DEAD_END}:
            return RunView(**_run_dict(run))

        task = app.state.tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()

        # tear the sandbox down directly — the orchestrator's cleanup may not
        # run if the task was cancelled while holding a stuck session
        mgr = app.state.system.sandbox_manager
        if mgr is not None and run.sandbox_container:
            try:
                await mgr.stop_by_name(run.sandbox_container)
            except Exception:  # noqa: BLE001
                pass

        run.status = RunStatus.FAILED
        run.detail = "cancelled by user"
        run.ended_at = run.ended_at or time.time()
        app.state.persist_runs()
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

        target = _validate_target_or_400(sys, req.tenant, req.target)

        errs = sys.replay.validate_params(artifact, req.params)
        if errs:
            raise HTTPException(422, {"error": "params do not match input_schema", "detail": errs})

        invocation_id = "inv_" + uuid.uuid4().hex[:12]
        run = RunRecord(
            run_id=invocation_id, mode=RunMode.REPLAY, tenant_id=req.tenant, app_target=target,
            artifact_id=artifact_id, artifact_version=req.version, params=req.params,
            name=artifact.name,
            # a replay reproduces an already-approved capability - it never
            # records a new draft, so mark it as such for the run UI.
            record_outcome="reused",
        )
        app.state.runs[invocation_id] = run
        app.state.persist_runs()

        async def _do() -> None:
            run.started_at = time.time()
            run.status = RunStatus.RUNNING
            try:
                result = await sys.replay.execute(
                    artifact, req.params, target=target, tenant=req.tenant,
                    run_id=invocation_id, idempotency_key=req.idempotency_key, run=run,
                    # an unrecoverable step blocks for a human hand-back (§3.6)
                    # rather than failing outright; tests call execute() directly
                    # with the default 0.0 and stay fast.
                    handoff_wait_s=900.0,
                )
            except Exception as exc:  # noqa: BLE001 - surface any replay crash on the run
                run.status = RunStatus.FAILED
                run.detail = f"replay error: {exc}"
                run.ended_at = time.time()
                app.state.persist_runs()
                return
            app.state.replays[invocation_id] = result
            run.status = RunStatus.COMPLETED if result.outcome.value in {"success", "recoverable_then_success", "business_outcome"} else RunStatus.FAILED
            run.detail = result.outcome.value
            run.ended_at = time.time()
            app.state.persist_runs()

        task = asyncio.create_task(_do())
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=req.wait_seconds)
        except TimeoutError:
            return {"invocation_id": invocation_id, "status": "running", "poll": f"/replays/{invocation_id}"}

        result = app.state.replays.get(invocation_id)
        if result is None:  # _do returned early after a crash - the run carries the detail
            return {"invocation_id": invocation_id, "status": run.status, "detail": run.detail}
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
        # group by (name, vendor_app_id) -> keep the highest approved version
        latest: dict[tuple[str, str], Any] = {}
        counts: dict[tuple[str, str], int] = {}
        for a in arts:
            key = (a.name, a.vendor_app_id)
            counts[key] = counts.get(key, 0) + 1
            if key not in latest or a.version > latest[key].version:
                latest[key] = a
        return [_capability_card(a, counts[(a.name, a.vendor_app_id)] - 1) for a in latest.values()]

    # -- ST-037..ST-040: escalation & operator console ---------------
    @app.get("/interventions")
    async def list_interventions(status: str | None = "open") -> list[dict[str, Any]]:
        return app.state.system.console.inbox(status=status or "open")

    @app.get("/runs/{run_id}/intervention")
    async def run_intervention(run_id: str) -> dict[str, Any] | None:
        """The latest not-resolved intervention for a run (open or claimed), so
        the console can keep the handoff controls after a claim."""
        svc = app.state.system.escalation
        active = [
            iv for iv in svc.list_interventions()
            if iv.run_id == run_id and iv.status != "resolved"
        ]
        if not active:
            return None
        iv = active[-1]
        return {
            "intervention_id": iv.intervention_id,
            "run_id": iv.run_id,
            "status": iv.status,
            "claimed_by": iv.claimed_by,
            "step_index": iv.step_index,
            "reason": iv.reason,
            "goal": iv.goal,
        }

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
                               "matched_kind", "matched_rank", "drift_signal", "url_after",
                               "timed_out", "error", "description", "code", "rule",
                               "recovery", "status")
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


def _normalize_target(sys: System, target: str) -> str:
    """From inside a Docker sandbox, localhost/127.0.0.1 mean the container, not
    the host — rewrite them to host.docker.internal so a human can just type the
    URL they'd use in their own browser. No-op when the sandbox is off."""
    if not (sys.sandbox_manager is not None):
        return target
    parsed = urlparse(target)
    if (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return target.replace(parsed.hostname, "host.docker.internal", 1)
    return target


def _validate_target_or_400(sys: System, tenant: str, target: str) -> str:
    parsed = urlparse(target)
    if not parsed.scheme or not parsed.hostname:
        raise HTTPException(422, f"target must be an absolute URL, got {target!r}")
    target = _normalize_target(sys, target)
    # The site the operator typed is the site they want tested — register its
    # host so navigation there is permitted. Egress to any other host is still
    # blocked and risky routes still require confirmation.
    sys.policy.allow_target(tenant, target)
    decision = sys.policy.check(
        ActionContext(tenant_id=tenant, action_type=ActionType.NAVIGATE, target_url=target)
    )
    if decision.verdict == PolicyVerdict.BLOCK:
        raise HTTPException(422, f"target not permitted for tenant {tenant!r}: {decision.reason}")
    return target


def _run_dict(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id, "mode": run.mode, "status": run.status, "tenant_id": run.tenant_id,
        "app_target": run.app_target, "goal": run.goal, "detail": run.detail, "step_count": run.step_count,
        "name": run.name, "params": run.params or {},
        "artifact_id": run.artifact_id, "artifact_version": run.artifact_version,
        "record_outcome": run.record_outcome,
        "novnc_url": run.novnc_url, "sandbox_container": run.sandbox_container,
        "browser": run.browser, "started_at": run.started_at, "ended_at": run.ended_at,
        "llm_calls": run.llm_calls, "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
    }


def _target_hint(step: Any) -> str | None:
    """A short human phrase for what a step acts on, from its top locator."""
    if step.action_type == "navigate":
        return None
    if step.value_binding and step.value_binding.param:
        base = step.value_binding.param
    else:
        base = None
    if not step.locator_spec:
        return base
    p = {k: v for k, v in step.locator_spec[0].params.items() if not k.startswith("_")}
    hint = (
        (f"{p['role']} “{p['name']}”" if p.get("role") and p.get("name") else None)
        or (f"“{p['name']}”" if p.get("name") else None)
        or (f"the {p['label']} field" if p.get("label") else None)
        or (f"near “{p['near']}”" if p.get("near") else None)
        or (f"“{p['text']}”" if p.get("text") else None)
        or (p.get("css"))
        or (p.get("role"))
    )
    if base and hint:
        return f"{base} → {hint}"
    return hint or base


def _capability_card(a: Any, older_versions: int) -> dict[str, Any]:
    ip = a.input_schema.get("properties", {})
    op = a.output_schema.get("properties", {})
    return {
        "name": a.name,
        "artifact_id": a.artifact_id,
        "version": a.version,
        "older_versions": older_versions,
        "confirmations": getattr(a, "confirmations", 0),
        "supersedes": getattr(a, "supersedes", None),
        "vendor_app_id": a.vendor_app_id,
        "app_version": a.app_version,
        "risk_class": a.risk_class,
        "goal": a.goal_description,
        "summary": a.agent_summary or a.goal_description,
        "summary_source": "model" if a.agent_summary else "goal",
        "inputs": [
            {
                "name": k,
                "type": v.get("type", "string"),
                "example": v.get("example"),
                "sensitive": bool(v.get("x-sensitive")),
            }
            for k, v in ip.items()
        ],
        "outputs": [
            {"field": k, "shape": v.get("x-shape", v.get("type", "string"))}
            for k, v in op.items()
        ],
        "steps": [
            {
                "i": s.step_index,
                "action": s.action_type,
                "description": s.description,
                "target": _target_hint(s),
                "idempotent": s.idempotent,
                "binding": (
                    {"param": s.value_binding.param} if s.value_binding and s.value_binding.param
                    else {"literal": s.value_binding.literal} if s.value_binding
                    else None
                ),
                "output": s.output_binding.field if s.output_binding else None,
                "checkpoint": (
                    {"kind": s.step_checkpoint.kind, "params": s.step_checkpoint.params}
                    if s.step_checkpoint else None
                ),
                "locators": [
                    {"kind": ls.kind, "rank": ls.rank,
                     "params": {k: v for k, v in ls.params.items() if not k.startswith("_")},
                     "rationale": ls.rationale}
                    for ls in s.locator_spec
                ],
            }
            for s in a.steps
        ],
        "handles": {
            "business_outcomes": [
                {"code": r.code, "message": r.message} for r in a.known_outcomes
            ],
            "recoverable": [r.name for r in a.recoverable_rules],
        },
        "provenance": {
            "created_from_run_id": a.created_from_run_id,
            "reviewed_by": a.reviewed_by,
            "reviewed_at": a.reviewed_at,
        },
        "input_schema": a.input_schema,
        "output_schema": a.output_schema,
        "invoke": f"/replays/{a.artifact_id}/invoke",
    }


def _artifact_summary(a: Any) -> dict[str, Any]:
    return {
        "artifact_id": a.artifact_id, "version": a.version, "name": a.name, "status": a.status,
        "goal": a.goal_description, "vendor_app_id": a.vendor_app_id, "app_version": a.app_version,
        "tenant_scope": a.tenant_scope.model_dump(), "risk_class": a.risk_class,
        "steps": len(a.steps), "known_outcomes": [r.code for r in a.known_outcomes],
    }


_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "at", "with",
    "their", "them", "this", "that", "then", "up", "read", "get", "find", "look",
    "current", "please", "new",
}


def _slug(text: str) -> str:
    import re

    words = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in _STOPWORDS]
    slug = "_".join(words[:5])[:48].strip("_")
    return slug or "capability"
