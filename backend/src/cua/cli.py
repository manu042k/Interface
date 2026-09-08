"""`cua` command-line — the demo path from the README.

    cua serve-mock                       # start the MockBank target app
    cua discover --goal "..." --target URL [--params member_id=12345] [--name cap]
    cua artifacts [--status draft]
    cua approve <artifact_id> <version> --reviewer you
    cua replay <artifact_id> --version 1 --target URL [--params member_id=00000]
    cua serve                            # start the HTTP gateway

Discovery and replay write an evidence bundle (structured log + screenshots +
artifact / result JSON) under --out (default: ../evidence/<kind>-<runid>/).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .assembly import build_system
from .config import load_config
from .models import RunStatus

app = typer.Typer(add_completion=False, help=__doc__)
console = Console()

_EVIDENCE_DEFAULT = Path(__file__).resolve().parents[3] / "evidence"


def _kv(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise typer.BadParameter(f"expected key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _dump_run_evidence(sink_root: Path, run_id: str, out_dir: Path, extra: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(sink_root) / run_id
    if src.exists():
        for f in src.iterdir():
            shutil.copy2(f, out_dir / f.name)
    (out_dir / "summary.json").write_text(json.dumps(extra, indent=2, default=str), encoding="utf-8")
    console.print(f"[green]evidence written[/green] -> {out_dir}")


@app.command("serve-mock")
def serve_mock(host: str = "127.0.0.1", port: int = 8799) -> None:
    """Run the MockBank legacy target app."""
    from mockbank.app import app as flask_app

    console.print(f"MockBank on http://{host}:{port}")
    flask_app.run(host=host, port=port, threaded=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the HTTP gateway (FastAPI/uvicorn)."""
    import uvicorn

    uvicorn.run("cua.api.gateway:create_app", factory=True, host=host, port=port)


@app.command()
def discover(
    goal: str = typer.Option(..., help="natural-language goal"),
    target: str = typer.Option(..., help="entry-point URL on the target app"),
    tenant: str = "default",
    params: list[str] = typer.Option(None, "--params", "-p", help="key=value (repeatable)"),
    name: str | None = typer.Option(None, help="capability name to record under"),
    vendor_app_id: str = "mockbank",
    confirm_risky: bool = typer.Option(False, help="pre-authorize risky/irreversible steps"),
    out: Path | None = typer.Option(None, help="evidence output dir"),
) -> None:
    """Run one LLM-driven discovery run and record a draft artifact."""
    cfg = load_config(strict=False)
    system = build_system(cfg)

    async def _go():
        run, transcript = await system.orchestrator.run_discovery(
            goal=goal, target=target, tenant=tenant, params=_kv(params), confirm_risky=confirm_risky
        )
        artifact = None
        if run.status == RunStatus.COMPLETED:
            artifact = system.record(transcript, name=name or _slug(goal), vendor_app_id=vendor_app_id)
        await system.shutdown()
        return run, transcript, artifact

    run, transcript, artifact = asyncio.run(_go())
    console.print(f"run [bold]{run.run_id}[/bold] -> [yellow]{run.status}[/yellow] ({run.detail})")
    if artifact:
        console.print(f"artifact [bold]{artifact.artifact_id}[/bold] v{artifact.version} (status={artifact.status})")

    out_dir = (out or (_EVIDENCE_DEFAULT / f"discovery-{run.run_id}"))
    summary = {
        "kind": "discovery", "run_id": run.run_id, "goal": goal, "target": target,
        "status": run.status, "detail": run.detail, "step_count": run.step_count,
        "intervention_id": transcript.intervention_id,
        "artifact": artifact.model_dump() if artifact else None,
        "transcript": [
            {
                "step": e.step, "tool": e.tool_call.tool, "reasoning": e.tool_call.reasoning,
                "guardrail": e.guardrail_verdict, "ok": e.action_ok, "result": e.action_result,
            }
            for e in transcript.entries
        ],
    }
    _dump_run_evidence(cfg.evidence_root, run.run_id, out_dir, summary)


@app.command()
def artifacts(status: str | None = None) -> None:
    """List recorded artifacts."""
    system = build_system(load_config(strict=False))
    rows = system.store.list(status=status)
    t = Table("artifact_id", "v", "name", "status", "risk", "steps", "reviewed_by")
    for a in rows:
        t.add_row(a.artifact_id, str(a.version), a.name, a.status, a.risk_class, str(len(a.steps)), a.reviewed_by or "-")
    console.print(t)
    asyncio.run(system.shutdown())


@app.command()
def approve(
    artifact_id: str, version: int,
    reviewer: str = typer.Option(..., help="who is approving"),
    reject: bool = typer.Option(False, help="reject instead of approve"),
    notes: str = "",
) -> None:
    """Promote a draft artifact to approved (or reject it)."""
    system = build_system(load_config(strict=False))
    art = system.store.promote(
        artifact_id, version, "reject" if reject else "approve", reviewer=reviewer, notes=notes or None
    )
    console.print(f"{artifact_id} v{version} -> [bold]{art.status}[/bold]")
    asyncio.run(system.shutdown())


@app.command()
def replay(
    artifact_id: str,
    version: int = typer.Option(..., help="explicit version — never 'latest'"),
    target: str = typer.Option(..., help="entry-point URL"),
    tenant: str = "default",
    params: list[str] = typer.Option(None, "--params", "-p", help="key=value (repeatable)"),
    idempotency_key: str | None = None,
    out: Path | None = typer.Option(None, help="evidence output dir"),
) -> None:
    """Deterministically replay an approved artifact."""
    cfg = load_config(strict=False)
    system = build_system(cfg)
    art = system.store.get(artifact_id, version)

    async def _go():
        rid = f"replay-{art.artifact_id[:8]}-{int(__import__('time').time())}"
        result = await system.replay.execute(
            art, _kv(params), target=target, tenant=tenant, run_id=rid, idempotency_key=idempotency_key
        )
        await system.shutdown()
        return rid, result

    rid, result = asyncio.run(_go())
    color = {"success": "green", "recoverable_then_success": "green",
             "business_outcome": "yellow", "hard_failure": "red"}.get(result.outcome, "white")
    console.print(f"[{color}]{result.outcome}[/{color}]  outputs={result.outputs} "
                  f"code={result.business_outcome_code} recovered={result.recovered_conditions}")
    if result.failure_detail:
        console.print(f"  failure: step {result.failure_detail.step_index}: "
                      f"expected {result.failure_detail.expected!r}, observed {result.failure_detail.observed!r}")

    out_dir = out or (_EVIDENCE_DEFAULT / rid)
    _dump_run_evidence(cfg.evidence_root, rid, out_dir, {
        "kind": "replay", "artifact_id": artifact_id, "version": version, "target": target,
        "params": _kv(params), "result": result.model_dump(),
    })


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "capability"


if __name__ == "__main__":
    app()
