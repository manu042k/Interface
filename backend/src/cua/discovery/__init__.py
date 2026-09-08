"""Discovery domain: the LLM observe -> decide -> act loop. The only domain that
calls a model for a *decision*. Produces a transcript; the Artifact Recorder
(Phase 5) turns a successful one into a CapabilityArtifact."""

from .agent import TOOL_SCHEMA, DiscoveryAgent
from .orchestrator import DiscoveryTranscript, Orchestrator, TranscriptEntry

__all__ = ["DiscoveryAgent", "TOOL_SCHEMA", "Orchestrator", "DiscoveryTranscript", "TranscriptEntry"]
