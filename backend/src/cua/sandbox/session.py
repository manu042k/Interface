"""Glue: acquire/release the live surface for a run, with or without a sandbox.

With a SandboxManager: spawn a container, attach the adapter over CDP, and record
the noVNC URL on the RunRecord. Without one: the plain headless adapter path.
Teardown skips the container when the run is STUCK, so a human can take over the
live sandbox exactly where automation stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import RunRecord, RunStatus
from .manager import SandboxHandle, SandboxManager, SandboxUnavailable


@dataclass
class RunSurface:
    session_handle: str
    sandbox: SandboxHandle | None = None


async def open_run_surface(
    *,
    adapter: Any,
    sandbox_manager: SandboxManager | None,
    target: str,
    tenant: str,
    run: RunRecord,
    logger: Any | None = None,
) -> RunSurface:
    if sandbox_manager is None:
        return RunSurface(session_handle=await adapter.open_session(target, tenant))

    try:
        handle = await sandbox_manager.spawn(target)
    except SandboxUnavailable as exc:
        if logger is not None:
            logger.event(None, "sandbox_unavailable", detail=str(exc))
        # graceful degradation: run headless, no live view
        return RunSurface(session_handle=await adapter.open_session(target, tenant))

    run.sandbox_container = handle.container
    run.novnc_url = handle.novnc_url
    run.cdp_url = handle.cdp_url
    if logger is not None:
        logger.event(None, "sandbox_started", container=handle.container, novnc_url=handle.novnc_url)

    session = await adapter.open_session(target, tenant, cdp_url=handle.cdp_url)
    return RunSurface(session_handle=session, sandbox=handle)


async def close_run_surface(
    *,
    adapter: Any,
    sandbox_manager: SandboxManager | None,
    surface: RunSurface,
    run: RunRecord,
    logger: Any | None = None,
) -> None:
    hold = run.status == RunStatus.STUCK
    try:
        await adapter.close_session(surface.session_handle)
    except Exception:  # noqa: BLE001
        pass
    if surface.sandbox is not None and sandbox_manager is not None and not hold:
        await sandbox_manager.stop(surface.sandbox)
        run.sandbox_container = None
        if logger is not None:
            logger.event(None, "sandbox_stopped", container=surface.sandbox.container)
    elif hold and logger is not None:
        logger.event(None, "sandbox_held", container=run.sandbox_container, reason="stuck — awaiting operator")
