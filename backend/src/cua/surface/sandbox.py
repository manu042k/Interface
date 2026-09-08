"""ST-041/042: per-session isolation — the seam, partially enforced here.

WHAT IS REAL in this implementation:
  * one browser **context per session** (PlaywrightAdapter) — cookies, storage,
    cache die with the session; no cross-run reuse;
  * **cross-host egress blocked at the context route handler** — a request to any
    host not on the session's allowlist is aborted, not just flagged;
  * a **wall-clock watchdog** per session that force-closes a runaway session and
    surfaces `resource_exceeded` with evidence.

WHAT IS DESIGN-ONLY (documented in REPORT.md §Cuts):
  * a real container / microVM (Firecracker/gVisor) per session with kernel-level
    CPU/memory ceilings and a warm pool for cold-start latency;
  * network egress enforced at the sandbox boundary rather than in-process.

The interface a container orchestrator would drive is `SessionWatchdog` + the
adapter's `open_session(..., extra_allowed_hosts=...)`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SessionBudget:
    wall_clock_s: float = 300.0
    # CPU / memory ceilings are container-level in production; carried here so the
    # contract is visible even though enforcement is design-only.
    cpu_seconds: float | None = None
    memory_mb: int | None = None


@dataclass
class SessionMeter:
    session_id: str
    started_at: float = field(default_factory=time.time)
    budget: SessionBudget = field(default_factory=SessionBudget)
    killed: bool = False
    kill_reason: str | None = None

    @property
    def age_s(self) -> float:
        return time.time() - self.started_at

    def over_budget(self) -> str | None:
        if self.age_s > self.budget.wall_clock_s:
            return f"wall_clock exceeded ({self.age_s:.0f}s > {self.budget.wall_clock_s:.0f}s)"
        return None


class SessionWatchdog:
    """Tracks per-session budgets; `sweep()` returns sessions that must be killed.
    A caller (adapter / orchestration layer) does the actual teardown so this
    stays free of a hard dependency on the adapter."""

    def __init__(self, default_budget: SessionBudget | None = None) -> None:
        self._meters: dict[str, SessionMeter] = {}
        self._default = default_budget or SessionBudget()

    def register(self, session_id: str, budget: SessionBudget | None = None) -> SessionMeter:
        meter = SessionMeter(session_id=session_id, budget=budget or self._default)
        self._meters[session_id] = meter
        return meter

    def unregister(self, session_id: str) -> None:
        self._meters.pop(session_id, None)

    def meter(self, session_id: str) -> SessionMeter | None:
        return self._meters.get(session_id)

    def sweep(self) -> list[SessionMeter]:
        doomed: list[SessionMeter] = []
        for meter in self._meters.values():
            if meter.killed:
                continue
            reason = meter.over_budget()
            if reason:
                meter.killed = True
                meter.kill_reason = reason
                doomed.append(meter)
        return doomed

    def report(self) -> list[dict[str, object]]:
        return [
            {
                "session_id": m.session_id, "age_s": round(m.age_s, 1),
                "wall_clock_budget_s": m.budget.wall_clock_s,
                "killed": m.killed, "kill_reason": m.kill_reason,
            }
            for m in self._meters.values()
        ]
