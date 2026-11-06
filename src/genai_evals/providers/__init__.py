"""Provider registry: one profile name -> one Provider instance (shared rate limiter)."""
from __future__ import annotations

from ..config import ProviderProfile, ProvidersConfig
from .base import Provider, ProviderResponse, RateLimiter


def build_provider(profile: ProviderProfile, client=None) -> Provider:
    p = profile.provider
    if p == "openai":
        from .openai_ import OpenAIResponsesProvider
        return OpenAIResponsesProvider(profile, client)
    if p == "openai_compatible":
        from .openai_ import OpenAICompatibleProvider
        return OpenAICompatibleProvider(profile, client)
    if p in ("anthropic", "vertex_anthropic"):
        from .anthropic_ import AnthropicProvider
        return AnthropicProvider(profile, client)
    if p in ("bedrock", "bedrock_anthropic"):
        from .bedrock import BedrockConverseProvider
        return BedrockConverseProvider(profile, client)
    if p == "vertex":
        from .vertex import VertexGeminiProvider
        return VertexGeminiProvider(profile, client)
    if p == "mock":
        from .mock import MockProvider
        return MockProvider(profile, client)
    raise ValueError(f"unknown provider {p}")


class ProviderPool:
    def __init__(self, config: ProvidersConfig, clients: dict | None = None):
        self.config = config
        self._clients = clients or {}
        self._cache: dict[str, Provider] = {}

    def get(self, name: str) -> Provider:
        if name not in self._cache:
            self._cache[name] = build_provider(self.config.get(name), self._clients.get(name))
        return self._cache[name]

    def __contains__(self, name: str) -> bool:
        return name in self.config.profiles


__all__ = ["Provider", "ProviderResponse", "RateLimiter", "ProviderPool", "build_provider"]
