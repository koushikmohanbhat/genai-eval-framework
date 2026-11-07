"""Provider-neutral model interface. Every target, judge, simulator and embedding call goes through a profile."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import ProviderProfile
from ..models import Message, ToolCall, Usage


@dataclass
class ProviderResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    model: str | None = None
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)   # guardrail / safety results, response ids


class RateLimiter:
    """Concurrency cap plus a simple requests-per-minute spacing."""

    def __init__(self, max_concurrency: int, rpm: int | None):
        self.sem = asyncio.Semaphore(max(1, max_concurrency))
        self.interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self.sem.acquire()
        if self.interval:
            async with self._lock:
                now = time.monotonic()
                wait = self._next - now
                self._next = max(now, self._next) + self.interval
            if wait > 0:
                await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc):
        self.sem.release()


class Provider:
    """Subclasses implement `_complete` (sync or async) and optionally `_embed`."""

    retry_base_s = 1.0

    def __init__(self, profile: ProviderProfile, client: Any = None):
        self.profile = profile
        self._client = client
        self._limiter: RateLimiter | None = None

    @property
    def limiter(self) -> RateLimiter:
        if self._limiter is None:
            self._limiter = RateLimiter(self.profile.max_concurrency, self.profile.rpm)
        return self._limiter

    @property
    def name(self) -> str:
        return self.profile.name

    async def complete(self, messages: list[Message], tools: list[dict] | None = None, *, temperature: float | None = None,
                       max_tokens: int | None = None, json_schema: dict | None = None, json_mode: bool = False) -> ProviderResponse:
        opts = dict(self.profile.options)
        temperature = opts.pop("temperature", None) if temperature is None else temperature
        max_tokens = max_tokens or opts.pop("max_tokens", None)
        last: Exception | None = None
        for attempt in range(self.profile.retries + 1):
            try:
                async with self.limiter:
                    t0 = time.perf_counter()
                    res = await asyncio.to_thread(self._complete, messages, tools or [], temperature, max_tokens,
                                                  json_schema, json_mode)
                    if not res.latency_ms:
                        res.latency_ms = (time.perf_counter() - t0) * 1000
                    res.model = res.model or self.profile.model
                    return res
            except Exception as e:  # noqa: BLE001 - provider SDKs raise many types
                last = e
                if not self.retryable(e) or attempt == self.profile.retries:
                    raise
                await asyncio.sleep(self.retry_base_s * (2 ** attempt) * (0.5 + random.random()))
        raise last  # pragma: no cover

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self.limiter:
            return await asyncio.to_thread(self._embed, texts)

    def retryable(self, e: Exception) -> bool:
        from ..retry import is_transient
        return is_transient(e)

    def cost(self, usage: Usage) -> float | None:
        p = self.profile.pricing
        if not p:
            return None
        cached = usage.cached_tokens if p.cached_input_per_mtok is not None else 0
        return ((usage.input_tokens - cached) * p.input_per_mtok + cached * (p.cached_input_per_mtok or 0)
                + usage.output_tokens * p.output_per_mtok) / 1e6

    # ---- implemented by subclasses ---------------------------------------
    def _complete(self, messages: list[Message], tools: list[dict], temperature: float | None, max_tokens: int | None,
                  json_schema: dict | None, json_mode: bool) -> ProviderResponse:  # pragma: no cover
        raise NotImplementedError

    def _embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(f"provider '{self.profile.provider}' does not support embeddings")


def split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    sys = [m.content or "" for m in messages if m.role in ("system", "developer")]
    rest = [m for m in messages if m.role not in ("system", "developer")]
    return ("\n\n".join(sys) if sys else None), rest
