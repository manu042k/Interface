"""Cross-run metrics: token/cost trend, reliability, and the amortisation
signal that is the whole point of record-once / replay-many.

`compute_metrics` is a pure function over the persisted RunRecords (+ artifact
counts), so it is cheap to call per request and trivial to test.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .models import RunMode, RunRecord, RunStatus

# Discovery outcomes we count as "the agent finished the goal".
_DISCOVERY_OK = {RunStatus.COMPLETED}
_DISCOVERY_HUMAN = {RunStatus.STUCK}
_DISCOVERY_DEAD = {RunStatus.DEAD_END, RunStatus.FAILED}
# Replay run.status is COMPLETED for success / recoverable / business_outcome,
# FAILED for a hard failure (see gateway.invoke_replay).
_REPLAY_OK = {RunStatus.COMPLETED}


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return round(s[k], 1)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def compute_metrics(
    runs: list[RunRecord],
    *,
    artifacts_total: int = 0,
    artifacts_approved: int = 0,
    artifacts_draft: int = 0,
    cost_per_mtok_in: float = 0.15,
    cost_per_mtok_out: float = 0.60,
) -> dict[str, Any]:
    runs = sorted(runs, key=lambda r: r.started_at)
    disc = [r for r in runs if r.mode == RunMode.DISCOVERY]
    rep = [r for r in runs if r.mode == RunMode.REPLAY]

    tok_in = sum(r.tokens_in for r in runs)
    tok_out = sum(r.tokens_out for r in runs)
    llm_calls = sum(r.llm_calls for r in runs)
    cost = tok_in / 1e6 * cost_per_mtok_in + tok_out / 1e6 * cost_per_mtok_out

    disc_ok = [r for r in disc if r.status in _DISCOVERY_OK]
    disc_human = [r for r in disc if r.status in _DISCOVERY_HUMAN]
    disc_dead = [r for r in disc if r.status in _DISCOVERY_DEAD]
    disc_bo = [r for r in disc if r.status == RunStatus.BUSINESS_OUTCOME]
    rep_ok = [r for r in rep if r.status in _REPLAY_OK]

    avg_tok_disc = round(sum(r.tokens_in + r.tokens_out for r in disc) / len(disc), 0) if disc else 0.0
    avg_calls_disc = round(sum(r.llm_calls for r in disc) / len(disc), 1) if disc else 0.0
    avg_steps_disc = round(sum(r.step_count for r in disc) / len(disc), 1) if disc else 0.0

    # amortisation: every replay is an invocation that did NOT re-reason the UI.
    replay_share = _pct(len(rep), len(disc) + len(rep))
    tokens_saved = int(len(rep) * avg_tok_disc)
    cost_saved = tokens_saved / 1e6 * ((cost_per_mtok_in + cost_per_mtok_out) / 2)

    def _dur(rs: list[RunRecord]) -> list[float]:
        return [r.ended_at - r.started_at for r in rs if r.ended_at]

    # -- daily series (the "is it improving?" view) --------------------
    by_day: dict[str, list[RunRecord]] = defaultdict(list)
    for r in runs:
        by_day[_day(r.started_at)].append(r)
    series = []
    cum_disc = cum_rep = 0
    for d in sorted(by_day):
        day_runs = by_day[d]
        dd = [r for r in day_runs if r.mode == RunMode.DISCOVERY]
        rr = [r for r in day_runs if r.mode == RunMode.REPLAY]
        cum_disc += len(dd)
        cum_rep += len(rr)
        dd_tok = [r.tokens_in + r.tokens_out for r in dd]
        dd_ok = [r for r in dd if r.status in _DISCOVERY_OK]
        series.append({
            "date": d,
            "discovery_runs": len(dd),
            "replay_runs": len(rr),
            "tokens": sum(dd_tok) + sum(r.tokens_in + r.tokens_out for r in rr),
            "avg_tokens_per_discovery": round(sum(dd_tok) / len(dd), 0) if dd else 0.0,
            "avg_llm_calls_per_discovery": round(sum(r.llm_calls for r in dd) / len(dd), 1) if dd else 0.0,
            "discovery_success_rate": _pct(len(dd_ok), len(dd)),
            "replay_share_to_date": _pct(cum_rep, cum_disc + cum_rep),
        })

    # -- trend verdict: first half vs second half of discovery runs ----
    def _avg_tok_per_ok(chunk: list[RunRecord]) -> float:
        oks = [r for r in chunk if r.status in _DISCOVERY_OK]
        t = sum(r.tokens_in + r.tokens_out for r in oks)
        return round(t / len(oks), 0) if oks else 0.0

    trend = {"direction": "flat", "detail": "not enough runs yet"}
    if len(disc) >= 6:
        half = len(disc) // 2
        early, late = _avg_tok_per_ok(disc[:half]), _avg_tok_per_ok(disc[half:])
        early_share = _pct(
            len([r for r in rep if r.started_at <= disc[half - 1].started_at]),
            half + len([r for r in rep if r.started_at <= disc[half - 1].started_at]),
        )
        delta = round(((late - early) / early * 100.0), 1) if early else 0.0
        better = (late and early and late < early) or replay_share > early_share
        trend = {
            "direction": "improving" if better else ("regressing" if delta > 10 else "flat"),
            "tokens_per_successful_discovery_early": early,
            "tokens_per_successful_discovery_late": late,
            "delta_pct": delta,
            "replay_share_now": replay_share,
            "detail": (
                f"tokens/successful discovery moved {early:.0f} -> {late:.0f} "
                f"({delta:+.1f}%); replay share {replay_share:.1f}%"
            ),
        }

    # -- per-capability (from replay invocations) ---------------------
    cap: dict[str, dict[str, Any]] = {}
    for r in rep:
        key = r.artifact_id or "(unrecorded)"
        c = cap.setdefault(key, {"artifact_id": r.artifact_id, "name": r.name, "invocations": 0, "ok": 0})
        c["invocations"] += 1
        if r.status in _REPLAY_OK:
            c["ok"] += 1
    top_caps = sorted(cap.values(), key=lambda c: c["invocations"], reverse=True)[:10]
    for c in top_caps:
        c["success_rate"] = _pct(c["ok"], c["invocations"])

    return {
        "generated_at": time.time(),
        "overview": {
            "total_runs": len(runs),
            "discovery_runs": len(disc),
            "replay_invocations": len(rep),
            "tokens_in": tok_in,
            "tokens_out": tok_out,
            "tokens_total": tok_in + tok_out,
            "llm_calls": llm_calls,
            "est_cost_usd": round(cost, 4),
            "cost_rate_per_mtok": {"in": cost_per_mtok_in, "out": cost_per_mtok_out},
        },
        "efficiency": {
            "avg_tokens_per_discovery": avg_tok_disc,
            "avg_llm_calls_per_discovery": avg_calls_disc,
            "avg_steps_per_discovery": avg_steps_disc,
            "replay_share_pct": replay_share,
            "tokens_saved_by_replay": tokens_saved,
            "est_cost_saved_usd": round(cost_saved, 4),
            "cost_per_successful_run_usd": round(cost / len(disc_ok), 4) if disc_ok else 0.0,
        },
        "trend": trend,
        "reliability": {
            "discovery": {
                "completed": len(disc_ok),
                "needs_human": len(disc_human),
                "dead_end_or_failed": len(disc_dead),
                "business_outcome": len(disc_bo),
                "success_rate_pct": _pct(len(disc_ok), len(disc)),
                "escalation_rate_pct": _pct(len(disc_human), len(disc)),
            },
            "replay": {
                "ok": len(rep_ok),
                "failed": len(rep) - len(rep_ok),
                "success_rate_pct": _pct(len(rep_ok), len(rep)),
            },
            "duration_seconds": {
                "discovery_p50": _percentile(_dur(disc), 50),
                "discovery_p95": _percentile(_dur(disc), 95),
                "replay_p50": _percentile(_dur(rep), 50),
                "replay_p95": _percentile(_dur(rep), 95),
            },
        },
        "capabilities": {
            "total": artifacts_total,
            "approved": artifacts_approved,
            "draft_pending_review": artifacts_draft,
            "most_invoked": top_caps,
        },
        "series": series,
    }
