"""Cross-run metrics aggregation."""

from __future__ import annotations

from cua.metrics import compute_metrics
from cua.models import RunMode, RunRecord, RunStatus


def _run(mode, status, *, tin=0, tout=0, calls=0, steps=0, t0=0.0, t1=None, aid=None, name=None):
    return RunRecord(
        run_id=f"r{t0}", mode=mode, app_target="http://x", status=status,
        tokens_in=tin, tokens_out=tout, llm_calls=calls, step_count=steps,
        started_at=t0, ended_at=t1, artifact_id=aid, name=name,
    )


def test_overview_and_amortisation():
    runs = [
        _run(RunMode.DISCOVERY, RunStatus.COMPLETED, tin=1000, tout=100, calls=10, steps=8, t0=1.0, t1=6.0),
        _run(RunMode.DISCOVERY, RunStatus.STUCK, tin=2000, tout=200, calls=20, steps=15, t0=2.0, t1=9.0),
        _run(RunMode.REPLAY, RunStatus.COMPLETED, t0=3.0, t1=4.0, aid="a1", name="cap"),
        _run(RunMode.REPLAY, RunStatus.FAILED, t0=4.0, t1=5.0, aid="a1", name="cap"),
    ]
    m = compute_metrics(runs, artifacts_total=3, artifacts_approved=2, artifacts_draft=1,
                        cost_per_mtok_in=1.0, cost_per_mtok_out=2.0)

    o = m["overview"]
    assert o["total_runs"] == 4
    assert o["discovery_runs"] == 2 and o["replay_invocations"] == 2
    assert o["tokens_total"] == 3300
    # 3000/1e6*1 + 300/1e6*2
    assert abs(o["est_cost_usd"] - (3000e-6 * 1.0 + 300e-6 * 2.0)) < 1e-9

    e = m["efficiency"]
    assert e["replay_share_pct"] == 50.0  # 2 replay / 4 total invocations
    # each replay "saved" an avg discovery of (1100+2200)/2 = 1650 tokens
    assert e["tokens_saved_by_replay"] == 2 * 1650

    r = m["reliability"]
    assert r["discovery"]["completed"] == 1 and r["discovery"]["needs_human"] == 1
    assert r["discovery"]["escalation_rate_pct"] == 50.0
    assert r["replay"]["ok"] == 1 and r["replay"]["failed"] == 1

    assert m["capabilities"]["most_invoked"][0]["invocations"] == 2
    assert m["capabilities"]["most_invoked"][0]["success_rate"] == 50.0


def test_trend_improving_when_tokens_per_success_drops():
    early = [_run(RunMode.DISCOVERY, RunStatus.COMPLETED, tin=5000, tout=0, t0=float(i)) for i in range(4)]
    late = [_run(RunMode.DISCOVERY, RunStatus.COMPLETED, tin=2000, tout=0, t0=float(10 + i)) for i in range(4)]
    m = compute_metrics(early + late)
    assert m["trend"]["direction"] == "improving"
    assert m["trend"]["tokens_per_successful_discovery_late"] < m["trend"]["tokens_per_successful_discovery_early"]


def test_empty_is_safe():
    m = compute_metrics([])
    assert m["overview"]["total_runs"] == 0
    assert m["trend"]["direction"] == "flat"
    assert m["series"] == []
