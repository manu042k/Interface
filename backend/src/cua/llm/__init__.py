"""LLM Provider Router — discovery-time only. Replay never imports this package
(ADR-07). The Discovery Agent calls `LLMRouter.call()`, never a provider SDK."""

from .providers import ModelResponse, OpenAICompatProvider, Provider, ScriptedProvider
from .router import AllProvidersExhausted, LLMRouter, ProviderHealth

__all__ = [
    "ModelResponse",
    "Provider",
    "OpenAICompatProvider",
    "ScriptedProvider",
    "LLMRouter",
    "ProviderHealth",
    "AllProvidersExhausted",
]
