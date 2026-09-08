"""Composition root — wires the modular-monolith components from a Config.

Everything is in-process (ADR-01). Swapping SQLite->Postgres, FileSink->object
store, or Playwright->a desktop adapter happens here, not in callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .discovery.agent import DiscoveryAgent
from .discovery.offline_pilot import offline_fallback
from .discovery.orchestrator import Orchestrator
from .events import RunLogger
from .llm.providers import OpenAICompatProvider, Provider, ScriptedProvider
from .llm.router import LLMRouter
from .observability import FileSink
from .policy.engine import PolicyEngine
from .surface.perception import Perception
from .surface.playwright_adapter import PlaywrightAdapter


@dataclass
class System:
    config: Config
    sink: FileSink
    adapter: PlaywrightAdapter
    perception: Perception
    policy: PolicyEngine
    router: LLMRouter
    agent: DiscoveryAgent
    orchestrator: Orchestrator

    def logger(self, run_id: str) -> RunLogger:
        return RunLogger(self.sink, run_id)

    async def shutdown(self) -> None:
        await self.adapter.shutdown()


def _build_providers(config: Config) -> list[Provider]:
    providers: list[Provider] = []
    for name in config.llm_providers:
        pc = config.providers[name]
        if name == "scripted":
            providers.append(ScriptedProvider(fallback=offline_fallback))
        else:
            providers.append(OpenAICompatProvider(name, pc.base_url, pc.api_key, pc.model))
    return providers


def build_system(config: Config, *, extra: dict[str, Any] | None = None) -> System:
    sink = FileSink(config.evidence_root)
    adapter = PlaywrightAdapter(headed=config.headed, default_timeout_ms=config.action_timeout_seconds * 1000)
    perception = Perception()
    policy = PolicyEngine.from_path(str(config.allowlist_path))

    router_logger = RunLogger(sink, "router")
    router = LLMRouter(_build_providers(config), logger=router_logger)
    agent = DiscoveryAgent(router)

    orchestrator = Orchestrator(
        config=config,
        adapter=adapter,
        perception=perception,
        agent=agent,
        policy=policy,
        router=router,
        logger_factory=lambda rid: RunLogger(sink, rid),
    )
    return System(config, sink, adapter, perception, policy, router, agent, orchestrator)
