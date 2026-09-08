"""ST-016: provider client abstraction.

One `Provider` interface. `OpenAICompatProvider` covers both configured real
providers (OpenRouter and NVIDIA NIM speak the OpenAI `/chat/completions` dialect
with tool-calling). `ScriptedProvider` is the offline, deterministic stand-in for
tests and the no-key demo path — it can replay a fixed script or run a tiny
rule-based fallback so an offline discovery run still completes the goal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class ProviderError(RuntimeError):
    """Base for provider failures the router inspects."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RateLimited(ProviderError):
    """429 / quota-exceeded — a *rotate*, not retry-in-place, signal."""


class ProviderUnavailable(ProviderError):
    """5xx / timeout / connection error."""


@dataclass
class ModelResponse:
    tool: str
    args: dict[str, Any]
    reasoning: str = ""
    provider: str = ""
    raw: Any = field(default=None, repr=False)


class Provider(Protocol):
    name: str

    async def complete(self, system: str, user: str, tools: list[dict[str, Any]]) -> ModelResponse: ...

    async def complete_text(self, system: str, user: str) -> str: ...


# ---------------------------------------------------------------------------
# Real providers (OpenAI-compatible)
# ---------------------------------------------------------------------------


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, *, timeout: float = 75.0) -> None:
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._timeout = timeout

    async def complete(self, system: str, user: str, tools: list[dict[str, Any]]) -> ModelResponse:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": tools,
            "tool_choice": "required",
            "temperature": 0,
            # cap the reservation — a tool call is small; also avoids OpenRouter
            # reserving the model's full context and 402-ing on a thin balance.
            "max_tokens": 1200,
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/chat/completions", json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            raise ProviderUnavailable(f"{self.name}: {exc}") from exc

        if resp.status_code == 429:
            ra = resp.headers.get("retry-after")
            raise RateLimited(f"{self.name}: 429", status=429, retry_after=_parse_retry_after(ra))
        if resp.status_code in {402, 403}:
            # payment / quota / credits — not going to recover this run; treat as
            # a rotate-and-cool signal so the router moves to the next provider.
            raise RateLimited(
                f"{self.name}: {resp.status_code} {resp.text[:160]}", status=resp.status_code
            )
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.name}: {resp.status_code}", status=resp.status_code)
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: {resp.status_code} {resp.text[:200]}", status=resp.status_code)

        data = resp.json()
        return _parse_openai_tool_call(data, self.name)

    async def complete_text(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 160,
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/chat/completions", json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            raise ProviderUnavailable(f"{self.name}: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimited(f"{self.name}: 429", status=429,
                              retry_after=_parse_retry_after(resp.headers.get("retry-after")))
        if resp.status_code in {402, 403}:
            raise RateLimited(f"{self.name}: {resp.status_code}", status=resp.status_code)
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.name}: {resp.status_code}", status=resp.status_code)
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: {resp.status_code} {resp.text[:200]}", status=resp.status_code)
        try:
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"{self.name}: unparseable text response: {exc}") from exc


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_openai_tool_call(data: dict[str, Any], provider: str) -> ModelResponse:
    try:
        choice = data["choices"][0]["message"]
        calls = choice.get("tool_calls") or []
        if not calls:
            # some models answer with content instead of a tool call
            raise ProviderError(f"{provider}: model returned no tool call")
        fn = calls[0]["function"]
        args = fn.get("arguments") or "{}"
        parsed = json.loads(args) if isinstance(args, str) else args
        reasoning = parsed.pop("reasoning", "") or choice.get("content") or ""
        return ModelResponse(tool=fn["name"], args=parsed, reasoning=reasoning, provider=provider, raw=data)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{provider}: unparseable response: {exc}") from exc


# ---------------------------------------------------------------------------
# Offline deterministic provider
# ---------------------------------------------------------------------------


class ScriptedProvider:
    """Deterministic. Either replays `script` (a list of {tool,args,reasoning})
    in order, or — if no script — runs `fallback(system, user)` to compute the
    next call from the prompt text."""

    name = "scripted"

    def __init__(
        self,
        script: list[dict[str, Any]] | None = None,
        *,
        fallback: Any | None = None,
    ) -> None:
        self._script = list(script or [])
        self._i = 0
        self._fallback = fallback

    async def complete(self, system: str, user: str, tools: list[dict[str, Any]]) -> ModelResponse:
        if self._i < len(self._script):
            item = self._script[self._i]
            self._i += 1
            return ModelResponse(
                tool=item["tool"],
                args=dict(item.get("args", {})),
                reasoning=item.get("reasoning", "scripted step"),
                provider=self.name,
            )
        if self._fallback is not None:
            tool, args, reasoning = self._fallback(system, user)
            return ModelResponse(tool=tool, args=args, reasoning=reasoning, provider=self.name)
        return ModelResponse(tool="stuck", args={"reason": "scripted provider exhausted", "context": {}}, provider=self.name)

    async def complete_text(self, system: str, user: str) -> str:
        # offline: no summary — the catalog falls back to the goal description.
        return ""
