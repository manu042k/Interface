"""ST-006/007: the surface-agnostic contract.

`SurfaceAdapter` is what the Orchestrator and Replay Executor talk to. They never
import Playwright. A desktop adapter (UI Automation / AX API) would implement the
same three-and-a-bit methods:

    open_session(target, tenant)  -> session_handle
    close_session(session_handle)
    execute(session_handle, action) -> ActionResult
    snapshot(session_handle)      -> raw material for Perception

`Action` is abstract: it carries either a human/structured `target_description`
(discovery, resolved by the adapter's built-in resolver) or a pre-resolved
`locator` dict (replay, chosen by the Locator Resolution Engine).
"""

from __future__ import annotations

import abc
from typing import Any

from pydantic import BaseModel, Field

from ..models import ActionResult, ActionType


class SurfaceError(RuntimeError):
    """Adapter-level failure that is not a normal ActionResult(ok=False)."""


class Action(BaseModel):
    type: ActionType
    # Discovery path: structured description {role,name,text,near,label,placeholder,css}
    # or a plain string. Replay path: leave None and pass `locator`.
    target_description: Any | None = None
    # Replay path: a concrete resolved locator, e.g. {"engine": "css", "value": "..."}
    locator: dict[str, Any] | None = None
    value: str | None = None
    # wait_for / assert_state
    condition: dict[str, Any] | None = None
    # extract
    expected_shape: str | None = None
    timeout_ms: int = 15000


class RawSnapshot(BaseModel):
    """What `snapshot()` returns for Perception to normalize."""

    url: str
    title: str
    ax_tree: list[dict[str, Any]] = Field(default_factory=list)
    html: str = ""
    screenshot_png: bytes | None = None
    # visible page text (innerText, trimmed) — the most reliable signal on hostile
    # legacy markup where the AX tree is empty and tags are non-semantic.
    visible_text: str = ""
    # live values of visible form controls — the serialized DOM does not reflect
    # what has been typed, so the agent can't otherwise tell a field is filled.
    form_values: list[dict[str, Any]] = Field(default_factory=list)


class SurfaceAdapter(abc.ABC):
    surface_family: str = "abstract"

    @abc.abstractmethod
    async def open_session(self, target: str, tenant: str = "default") -> str: ...

    @abc.abstractmethod
    async def close_session(self, session_handle: str) -> None: ...

    @abc.abstractmethod
    async def execute(self, session_handle: str, action: Action) -> ActionResult: ...

    @abc.abstractmethod
    async def snapshot(self, session_handle: str) -> RawSnapshot: ...

    async def read_text(self, session_handle: str, css: str) -> str | None:
        """Visible text of the first element matching `css`, or None. Used to
        lift a host's actionable error detail into a business-outcome result.
        Optional — surfaces without CSS return None."""
        return None

    # Optional lifecycle hooks used by escalation / sandbox layers.
    async def cdp_endpoint(self, session_handle: str) -> str | None:
        """Return a remote-control endpoint for a human handoff, if the surface
        supports one (CDP for a browser; a VNC/RDP URL for a desktop VM)."""
        return None
