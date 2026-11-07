"""Provider registry: one profile name -> one Provider instance (shared rate limiter)."""
from __future__ import annotations

from ..config import ProviderProfile, ProvidersConfig
from .base import Provider, ProviderResponse, RateLimiter


def build_provider(profile: ProviderProfile, client=None) -> Provider:
    """Look the provider up in the adapter registry (built-ins and `genai_evals.providers` entry points)."""
    from ..plugins import load
    return load("provider", profile.provider)(profile, client)


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
