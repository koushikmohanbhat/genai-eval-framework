"""Provider registry: one profile name -> one Provider instance (shared rate limiter)."""
from __future__ import annotations

from ..config import ProviderProfile, ProvidersConfig
from .base import Provider, ProviderResponse, RateLimiter


def build_provider(profile: ProviderProfile, client=None) -> Provider:
    """Look the provider up in the adapter registry (built-ins and `genai_evals.providers` entry points)."""
    from ..plugins import load
    return load("provider", profile.provider)(profile, client)


class BudgetExceeded(RuntimeError):
    """The run's --budget-usd was reached: no further model calls are made."""


class SpendMeter:
    """Model spend of one run, from provider pricing. With a budget, calls stop once it is reached."""

    def __init__(self, budget_usd: float | None = None):
        self.budget_usd, self.spent_usd, self.calls, self.unpriced_calls, self.stopped = budget_usd, 0.0, 0, 0, 0

    def check(self) -> None:
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            self.stopped += 1
            raise BudgetExceeded(f"budget of ${self.budget_usd:.2f} reached (spent ${self.spent_usd:.2f}); call not made")

    def add(self, cost: float | None) -> None:
        self.calls += 1
        if cost is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += cost


class ProviderPool:
    def __init__(self, config: ProvidersConfig, clients: dict | None = None, budget_usd: float | None = None):
        self.config = config
        self._clients = clients or {}
        self._cache: dict[str, Provider] = {}
        self.meter = SpendMeter(budget_usd)

    def get(self, name: str) -> Provider:
        if name not in self._cache:
            p = build_provider(self.config.get(name), self._clients.get(name))
            p.meter = self.meter
            self._cache[name] = p
        return self._cache[name]

    def __contains__(self, name: str) -> bool:
        return name in self.config.profiles


__all__ = ["Provider", "ProviderResponse", "RateLimiter", "ProviderPool", "build_provider", "BudgetExceeded", "SpendMeter"]
