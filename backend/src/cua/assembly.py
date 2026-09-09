"""Composition root — wires the modular-monolith components from a Config.

Everything is in-process (ADR-01). Swapping SQLite->Postgres, FileSink->object
store, or Playwright->a desktop adapter happens here, not in callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .artifact.recorder import ArtifactRecorder
from .artifact.store import ArtifactStore
from .config import Config
from .discovery.agent import DiscoveryAgent
from .discovery.offline_pilot import offline_fallback
from .discovery.orchestrator import DiscoveryTranscript, Orchestrator
from .escalation.operator_console import OperatorConsole
from .escalation.service import EscalationService
from .escalation.session_broker import SessionBroker
from .events import RunLogger
from .llm.providers import OpenAICompatProvider, Provider, ScriptedProvider
from .llm.router import LLMRouter
from .models import ArtifactStatus, CapabilityArtifact
from .observability import FileSink
from .policy.engine import PolicyEngine
from .replay.executor import ReplayExecutor
from .replay.locator import LocatorResolutionEngine
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
    store: ArtifactStore
    recorder: ArtifactRecorder
    locator_engine: LocatorResolutionEngine
    replay: ReplayExecutor
    broker: SessionBroker
    escalation: EscalationService
    console: OperatorConsole
    sandbox_manager: Any | None = None

    def logger(self, run_id: str) -> RunLogger:
        return RunLogger(self.sink, run_id)

    def record(
        self, transcript: DiscoveryTranscript, *, name: str, vendor_app_id: str = "mockbank",
        app_version: str = "7.2",
    ) -> CapabilityArtifact:
        """Build + persist a capability from a successful discovery transcript,
        de-duplicating against what's already stored:

          - reproduces an existing version exactly  -> bump `confirmations`, reuse it
          - latest version is an un-reviewed draft  -> overwrite that draft in place
          - differs from the latest (approved) one  -> save vN+1 with `supersedes`
          - nothing stored yet                      -> save v1 draft

        `artifact.record_outcome` (transient) is set to one of
        new | new_version | reused | updated_draft.
        """
        built = self.recorder.build_artifact(
            transcript, name=name, vendor_app_id=vendor_app_id, app_version=app_version
        )
        fp = built.flow_fingerprint

        same = self.store.find_by_fingerprint(name, vendor_app_id, fp)
        if same is not None:
            confirmed = self.store.bump_confirmation(same.artifact_id, same.version, transcript.run_id)
            confirmed.record_outcome = "reused"
            return confirmed

        latest = self.store.latest(name, vendor_app_id)
        if latest is not None and latest.status == ArtifactStatus.DRAFT:
            saved = self.store.replace_draft(built, artifact_id=latest.artifact_id, version=latest.version)
            saved.record_outcome = "updated_draft"
            return saved

        if latest is not None:
            built.supersedes = latest.version
        saved = self.store.save_draft(built)
        saved.record_outcome = "new_version" if latest else "new"
        return saved

    async def summarize_capability(self, artifact: CapabilityArtifact) -> str:
        """Record-time: ask the model for a catalog reference blurb (never in
        replay). Best-effort — persists onto the row, returns "" on failure."""
        from .artifact.summarizer import summarize

        text = await summarize(artifact, self.router)
        if text:
            artifact.agent_summary = text
            self.store.set_summary(artifact.artifact_id, artifact.version, text)
        return text

    async def shutdown(self) -> None:
        await self.adapter.shutdown()
        if self.sandbox_manager is not None:
            try:
                await self.sandbox_manager.stop_all()
            except Exception:  # noqa: BLE001
                pass


def _build_providers(config: Config) -> list[Provider]:
    providers: list[Provider] = []
    for name in config.llm_providers:
        pc = config.providers[name]
        if name == "scripted":
            providers.append(ScriptedProvider(fallback=offline_fallback))
        else:
            providers.append(
                OpenAICompatProvider(name, pc.base_url, pc.api_key, pc.model, rpm=pc.rpm)
            )
    return providers


def build_system(config: Config, *, extra: dict[str, Any] | None = None) -> System:
    sink = FileSink(config.evidence_root)
    adapter = PlaywrightAdapter(headed=config.headed, default_timeout_ms=config.action_timeout_seconds * 1000)
    perception = Perception()
    policy = PolicyEngine.from_path(str(config.allowlist_path))

    router_logger = RunLogger(sink, "router")
    router = LLMRouter(_build_providers(config), logger=router_logger)
    agent = DiscoveryAgent(router)

    sandbox_manager = None
    if config.use_sandbox:
        from .sandbox.manager import SandboxManager

        sandbox_manager = SandboxManager(image=config.sandbox_image, host=config.novnc_host)

    broker = SessionBroker()
    escalation = EscalationService(
        broker=broker, adapter=adapter, perception=perception,
        logger_factory=lambda rid: RunLogger(sink, rid),
    )
    console = OperatorConsole(escalation=escalation, adapter=adapter)

    orchestrator = Orchestrator(
        config=config,
        adapter=adapter,
        perception=perception,
        agent=agent,
        policy=policy,
        router=router,
        logger_factory=lambda rid: RunLogger(sink, rid),
        escalation=escalation,
        broker=broker,
        sandbox_manager=sandbox_manager,
    )
    store = ArtifactStore(config.db_path)
    recorder = ArtifactRecorder()
    locator_engine = LocatorResolutionEngine(logger=RunLogger(sink, "locator"))
    replay = ReplayExecutor(
        adapter=adapter,
        perception=perception,
        policy=policy,
        locator_engine=locator_engine,
        logger_factory=lambda rid: RunLogger(sink, rid),
        sandbox_manager=sandbox_manager,
        escalation=escalation,
        broker=broker,
        watch_delay_ms=config.replay_watch_delay_ms,
    )
    return System(
        config, sink, adapter, perception, policy, router, agent, orchestrator,
        store, recorder, locator_engine, replay, broker, escalation, console,
        sandbox_manager,
    )
