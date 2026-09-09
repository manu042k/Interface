"""ST-017: the rotating router.

Behaviour (TDD §1.5):
  * route to the highest-preference `healthy` provider;
  * on 429 / quota / sustained 5xx-timeout: mark it `cooling_down` (TTL from
    Retry-After when present, else a backoff), and immediately retry the SAME
    logical request against the next provider — rotate, don't retry-in-place;
  * a provider with a persistently high error rate is `degraded` and skipped;
  * if every provider is cooling_down/degraded -> raise `AllProvidersExhausted`
    (a distinct condition the Orchestrator pauses on, ST-020);
  * log which provider served each call.

State is in-memory (single-process monolith). A Redis-backed record is the
scale-out swap and doesn't change this interface.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .providers import (
    ModelResponse,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)


class ProviderStatus(StrEnum):
    HEALTHY = "healthy"
    COOLING_DOWN = "cooling_down"
    DEGRADED = "degraded"


@dataclass
class ProviderHealth:
    name: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    cooldown_until: float = 0.0
    recent_errors: list[float] = field(default_factory=list)  # timestamps
    served: int = 0
    # client-side pacing: min seconds between calls (0 = off) and the earliest
    # monotonic time the next call to this provider may start.
    min_interval: float = 0.0
    next_earliest: float = 0.0

    def note_error(self, now: float) -> None:
        self.recent_errors = [t for t in self.recent_errors if now - t < 120] + [now]

    def error_rate(self, now: float) -> float:
        recent = [t for t in self.recent_errors if now - t < 120]
        window = max(self.served + len(recent), 1)
        return len(recent) / window

    def available(self, now: float) -> bool:
        if self.status == ProviderStatus.COOLING_DOWN and now >= self.cooldown_until:
            self.status = ProviderStatus.HEALTHY
        return self.status == ProviderStatus.HEALTHY


class AllProvidersExhausted(RuntimeError):
    """Every configured provider is cooling_down or degraded simultaneously."""


class LLMRouter:
    def __init__(
        self,
        providers: list[Provider],
        *,
        default_cooldown: float = 30.0,
        degrade_error_rate: float = 0.5,
        logger: Any | None = None,
    ) -> None:
        if not providers:
            raise ValueError("router needs at least one provider")
        self._providers = providers
        self._health = {}
        for p in providers:
            rpm = int(getattr(p, "rpm", 0) or 0)
            self._health[p.name] = ProviderHealth(
                p.name, min_interval=(60.0 / rpm if rpm > 0 else 0.0)
            )
        self._default_cooldown = default_cooldown
        self._degrade_rate = degrade_error_rate
        self._log = logger
        self._run_log: Any | None = None

    @property
    def health(self) -> dict[str, ProviderHealth]:
        return self._health

    def _emit(self, event: str, **fields: Any) -> None:
        for lg in (self._log, self._run_log):
            if lg is not None:
                lg.event(None, event, **fields)

    async def _pace(self, h: ProviderHealth) -> None:
        """Client-side rate limit: hold the call until this provider's per-minute
        budget allows it, so a burst never trips the upstream 429."""
        if h.min_interval <= 0:
            return
        wait = h.next_earliest - time.monotonic()
        if wait > 0:
            self._emit("provider_throttle", provider=h.name, wait_s=round(wait, 2))
            await asyncio.sleep(wait)
        h.next_earliest = time.monotonic() + h.min_interval

    async def call(
        self, system: str, user: str, tools: list[dict[str, Any]], *, logger: Any | None = None
    ) -> ModelResponse:
        # per-turn provider is logged into the caller's run log too (TDD §1.5)
        self._run_log = logger
        now = time.time()
        tried: list[str] = []
        last_exc: Exception | None = None

        # with a single configured provider a transient blip has nowhere to
        # rotate — retry it in place a couple of times before cooling it down.
        solo_retries = 2 if len(self._providers) == 1 else 0

        for provider in self._providers:
            h = self._health[provider.name]
            if not h.available(now):
                continue
            tried.append(provider.name)
            retry = 0
            while True:
                try:
                    await self._pace(h)
                    resp = await provider.complete(system, user, tools)
                    h.served += 1
                    self._emit("llm_call", provider=provider.name, tool=resp.tool, rotated=len(tried) > 1)
                    return resp
                except RateLimited as exc:
                    last_exc = exc
                    ttl = exc.retry_after or self._default_cooldown
                    h.status = ProviderStatus.COOLING_DOWN
                    h.cooldown_until = time.time() + ttl
                    h.note_error(time.time())
                    self._emit("provider_rotate", provider=provider.name, reason="rate_limited", cooldown_s=ttl)
                    break
                except ProviderUnavailable as exc:
                    last_exc = exc
                    h.note_error(time.time())
                    if retry < solo_retries:
                        retry += 1
                        self._emit("provider_retry", provider=provider.name, attempt=retry, reason="unavailable")
                        await asyncio.sleep(2.0 * retry)
                        continue
                    if h.error_rate(time.time()) >= self._degrade_rate:
                        h.status = ProviderStatus.DEGRADED
                        self._emit("provider_degraded", provider=provider.name)
                    else:
                        h.status = ProviderStatus.COOLING_DOWN
                        h.cooldown_until = time.time() + self._default_cooldown
                    self._emit("provider_rotate", provider=provider.name, reason="unavailable")
                    break
                except ProviderError as exc:
                    last_exc = exc
                    h.note_error(time.time())
                    if retry < solo_retries:
                        retry += 1
                        self._emit("provider_retry", provider=provider.name, attempt=retry, reason="error")
                        await asyncio.sleep(2.0 * retry)
                        continue
                    self._emit("provider_rotate", provider=provider.name, reason="error")
                    break

        raise AllProvidersExhausted(
            f"no healthy provider (tried={tried or 'none'}; last error: {last_exc})"
        )

    async def call_text(self, system: str, user: str) -> str:
        """Plain-text completion for record-time metadata (capability summaries).
        Best-effort: returns "" if every provider is unavailable rather than
        raising — a missing summary is not fatal."""
        now = time.time()
        for provider in self._providers:
            h = self._health[provider.name]
            if not h.available(now):
                continue
            try:
                await self._pace(h)
                out = await provider.complete_text(system, user)
                h.served += 1
                return out
            except (ProviderUnavailable, ProviderError):
                h.note_error(time.time())
                continue
        return ""

    def reset(self) -> None:
        for h in self._health.values():
            h.status = ProviderStatus.HEALTHY
            h.cooldown_until = 0.0
            h.recent_errors.clear()
