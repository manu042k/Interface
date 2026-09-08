"""Execution domain: deterministic replay. Never imports the discovery/LLM
packages (ADR-07). Locator Resolution Engine + Replay Executor + the result
contract returned to the calling agent."""

from .locator import LocatorResolutionEngine, Resolution

__all__ = ["LocatorResolutionEngine", "Resolution"]
