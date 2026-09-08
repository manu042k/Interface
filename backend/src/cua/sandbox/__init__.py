"""Per-run Docker sandbox: one ephemeral container running a headed browser on a
virtual X display, exposed live over noVNC and drivable over CDP. This is the
concrete implementation of the "container/microVM per session" isolation model
(ADR-09) — previously design-only, now real on a single Docker host.
"""

from .manager import SandboxHandle, SandboxManager, SandboxUnavailable

__all__ = ["SandboxManager", "SandboxHandle", "SandboxUnavailable"]
