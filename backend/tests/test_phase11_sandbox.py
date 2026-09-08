"""Phase 11 — per-run Docker sandbox + live stream + report.

Skipped cleanly when Docker or the cua-sandbox image is unavailable, so the
offline suite (and CI) is unaffected.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
import pytest_asyncio

_DOCKER = shutil.which("docker") is not None


def _image_present() -> bool:
    if not _DOCKER:
        return False
    out = subprocess.run(
        ["docker", "images", "-q", "cua-sandbox:latest"], capture_output=True, text=True
    )
    return bool(out.stdout.strip())


pytestmark = pytest.mark.skipif(
    not _image_present(),
    reason="cua-sandbox:latest image not built (run backend/sandbox_image/build.sh)",
)


@pytest_asyncio.fixture
async def manager():
    from cua.sandbox.manager import SandboxManager

    m = SandboxManager()
    yield m
    await m.stop_all()


async def test_spawn_exposes_novnc_and_cdp_then_stops(manager):
    h = await manager.spawn("about:blank")
    assert h.container.startswith("cua-sandbox-")
    assert h.novnc_url.startswith("http://") and h.novnc_port > 1024
    assert h.cdp_url.startswith("http://")
    assert await manager.is_running(h.container)
    await manager.stop(h)
    assert not await manager.is_running(h.container)


async def test_adapter_drives_mockbank_inside_sandbox(manager, mockbank):
    from cua.models import ActionType
    from cua.surface.base import Action
    from cua.surface.playwright_adapter import PlaywrightAdapter

    # the sandbox reaches the host test server via host.docker.internal
    port = mockbank.rsplit(":", 1)[1]
    url = f"http://host.docker.internal:{port}/search"
    h = await manager.spawn(url)
    adapter = PlaywrightAdapter()
    try:
        sess = await adapter.open_session(url, cdp_url=h.cdp_url)
        snap = await adapter.snapshot(sess)
        assert "CoreServ" in snap.title
        r = await adapter.execute(
            sess, Action(type=ActionType.TYPE, target_description={"role": "textbox"}, value="12345")
        )
        assert r.ok
        await adapter.close_session(sess)
    finally:
        await adapter.shutdown()
        await manager.stop(h)


async def test_full_sandbox_discovery_and_report(offline_config, mockbank, monkeypatch):
    from cua.assembly import build_system
    from cua.models import RunStatus

    monkeypatch.setenv("MOCKBANK_INTERSTITIAL", "0")
    monkeypatch.setenv("CUA_USE_SANDBOX", "1")
    cfg = offline_config
    object.__setattr__(cfg, "use_sandbox", True)  # frozen dataclass
    system = build_system(cfg)
    try:
        port = mockbank.rsplit(":", 1)[1]
        run, transcript = await system.orchestrator.run_discovery(
            goal="look up member 12345 and read their current savings balance",
            target=f"http://host.docker.internal:{port}/search",
            params={"member_id": "12345"},
        )
        assert run.status == RunStatus.COMPLETED, run.detail
        assert run.novnc_url and run.novnc_url.startswith("http://")
        # sandbox stopped after a non-stuck run
        assert run.sandbox_container is None
        # events streamed to the sink and are replayable
        events = system.sink.read_events(run.run_id)
        assert any(e["event"] == "sandbox_started" for e in events)
        assert any(e["event"] == "run_finished" for e in events)
    finally:
        await system.shutdown()
