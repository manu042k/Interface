"""Capability domain: the Artifact — schema, versioning, review state, and
base/override resolution for multi-tenant reuse. The contract layer between
'what the model discovered' and 'what an agent can invoke'."""

from .recorder import ArtifactRecorder
from .store import ArtifactStore, PromotionDecision

__all__ = ["ArtifactRecorder", "ArtifactStore", "PromotionDecision"]
