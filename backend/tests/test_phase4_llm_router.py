"""Phase 4 — ST-016/017: LLM Provider Router."""

from __future__ import annotations

import pytest

from cua.llm.providers import ModelResponse, ProviderUnavailable, RateLimited
from cua.llm.router import AllProvidersExhausted, LLMRouter, ProviderStatus


class FakeProvider:
    def __init__(self, name, *, behavior="ok"):
        self.name = name
        self.behavior = behavior
        self.calls = 0

    async def complete(self, system, user, tools):
        self.calls += 1
        if self.behavior == "ok":
            return ModelResponse(tool="observe", args={}, provider=self.name)
        if self.behavior == "429":
            raise RateLimited(f"{self.name}: 429", status=429, retry_after=0.2)
        if self.behavior == "500":
            raise ProviderUnavailable(f"{self.name}: 500", status=500)
        raise AssertionError("bad behavior")


async def test_routes_to_first_healthy():
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    r = LLMRouter([p1, p2])
    resp = await r.call("s", "u", [])
    assert resp.provider == "p1" and p2.calls == 0


async def test_rotates_on_rate_limit_and_marks_cooling():
    p1 = FakeProvider("p1", behavior="429")
    p2 = FakeProvider("p2", behavior="ok")
    r = LLMRouter([p1, p2])
    resp = await r.call("s", "u", [])
    assert resp.provider == "p2"
    assert r.health["p1"].status == ProviderStatus.COOLING_DOWN
    assert r.health["p1"].cooldown_until > 0


async def test_cooldown_expires_and_provider_reenters():
    p1 = FakeProvider("p1", behavior="429")
    p2 = FakeProvider("p2", behavior="ok")
    r = LLMRouter([p1, p2], default_cooldown=0.05)
    await r.call("s", "u", [])  # p1 -> cooling, p2 serves
    import asyncio

    await asyncio.sleep(0.25)
    p1.behavior = "ok"
    resp = await r.call("s", "u", [])
    assert resp.provider == "p1"  # back in rotation after cooldown


async def test_all_exhausted_raises_distinct_error():
    p1 = FakeProvider("p1", behavior="429")
    p2 = FakeProvider("p2", behavior="429")
    r = LLMRouter([p1, p2])
    with pytest.raises(AllProvidersExhausted):
        await r.call("s", "u", [])


async def test_logs_which_provider_served():
    events = []

    class Log:
        def event(self, step, kind, **f):
            events.append((kind, f))

    p1 = FakeProvider("p1", behavior="429")
    p2 = FakeProvider("p2", behavior="ok")
    r = LLMRouter([p1, p2], logger=Log())
    await r.call("s", "u", [])
    served = [f for k, f in events if k == "llm_call"]
    assert served and served[0]["provider"] == "p2" and served[0]["rotated"] is True
