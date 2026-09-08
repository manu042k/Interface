"""ST-027/028/029: Locator Resolution Engine.

`resolve()` walks a recorded ranked strategy chain (ADR-02) against the current
SurfaceState and returns the first strategy that resolves to exactly one visible
element — reporting WHICH strategy matched and its rank. A match on anything but
rank 0 is a `drift_signal` (ST-028): the primary identifier degraded, which is
queryable as a trend before it becomes an outright failure.

No strategy resolving is a structured "unresolvable target" error naming every
strategy tried and why each failed — never a raw exception (ST-027).

Results are cached per `(artifact_version, surface_fingerprint, step_index)` with
singleflight coalescing so a burst of concurrent replays of a hot capability
doesn't stampede resolution (ST-029). A fingerprint change is a new key, so
stale entries are never served; `invalidate()` drops them explicitly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..models import LocatorStrategy


@dataclass
class Resolution:
    ok: bool
    matched_strategy: str | None = None
    matched_kind: str | None = None
    matched_rank: int | None = None
    concrete: dict[str, Any] | None = None  # target-description the executor acts on
    drift_signal: bool = False
    tried: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def as_event(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "matched_strategy": self.matched_strategy,
            "matched_kind": self.matched_kind,
            "matched_rank": self.matched_rank,
            "drift_signal": self.drift_signal,
            "tried": self.tried,
            "error": self.error,
        }


class LocatorResolutionEngine:
    def __init__(self, *, cache_size: int = 512, logger: Any | None = None) -> None:
        self._cache: dict[tuple, Resolution] = {}
        self._order: list[tuple] = []
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._cache_size = cache_size
        self._log = logger

    # -- ST-029 cache ------------------------------------------------
    def invalidate(self, *, fingerprint: str | None = None, artifact_version: int | None = None) -> int:
        before = len(self._cache)
        keep = {}
        for k, v in self._cache.items():
            av, fp, _ = k
            if fingerprint is not None and fp == fingerprint:
                continue
            if artifact_version is not None and av == artifact_version:
                continue
            keep[k] = v
        self._cache = keep
        self._order = [k for k in self._order if k in keep]
        return before - len(self._cache)

    def _cache_put(self, key: tuple, res: Resolution) -> None:
        self._cache[key] = res
        self._order.append(key)
        while len(self._order) > self._cache_size:
            old = self._order.pop(0)
            self._cache.pop(old, None)

    # -- ST-027 resolve -------------------------------------------
    async def resolve(
        self,
        locator_spec: list[LocatorStrategy],
        adapter: Any,
        session_handle: str,
        *,
        artifact_version: int,
        surface_fingerprint: str,
        step_index: int,
    ) -> Resolution:
        key = (artifact_version, surface_fingerprint, step_index)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:  # filled while we waited (singleflight)
                return cached
            res = await self._resolve_uncached(locator_spec, adapter, session_handle)
            self._cache_put(key, res)
            if self._log is not None:
                self._log.event(step_index, "locator_resolution", **res.as_event())
            return res

    async def _resolve_uncached(
        self, locator_spec: list[LocatorStrategy], adapter: Any, session_handle: str
    ) -> Resolution:
        ordered = sorted(locator_spec, key=lambda s: s.rank)
        tried: list[dict[str, Any]] = []
        for strat in ordered:
            outcome = await adapter.try_strategy(session_handle, strat.kind, strat.params)
            tried.append({
                "kind": strat.kind, "rank": strat.rank, "describe": outcome.get("describe"),
                "matched": outcome.get("matched"), "reason": outcome.get("reason"),
            })
            if outcome.get("matched"):
                drift = strat.rank > 0
                return Resolution(
                    ok=True,
                    matched_strategy=outcome.get("describe") or f"{strat.kind}",
                    matched_kind=strat.kind,
                    matched_rank=strat.rank,
                    concrete=outcome.get("target"),
                    drift_signal=drift,
                    tried=tried,
                )
        return Resolution(
            ok=False,
            tried=tried,
            error="unresolvable target: " + "; ".join(
                f"[rank {t['rank']} {t['kind']}] {t['reason']}" for t in tried
            ),
        )
