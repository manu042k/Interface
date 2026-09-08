"""Surface layer: the seam between 'how we perceive/act on a concrete app' and
'the recorded flow'. One adapter per surface family (Playwright web today; an
AX-API desktop driver behind the same interface tomorrow)."""

from .base import Action, SurfaceAdapter, SurfaceError
from .perception import Perception

__all__ = ["Action", "SurfaceAdapter", "SurfaceError", "Perception"]
