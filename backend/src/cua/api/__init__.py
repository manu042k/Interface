"""HTTP surface: Goal/Capability Gateway. Agent-facing product and operators
talk to this; everything behind it is the in-process monolith."""

from .gateway import create_app

__all__ = ["create_app"]
