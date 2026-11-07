"""Shared transient-error classification and retry with jittered exponential backoff."""
from __future__ import annotations

import random
import time
from typing import Any, Callable

TRANSIENT = ("ratelimit", "rate limit", "throttl", "timeout", "timed out", "429", "500", "502", "503", "504", "overloaded",
             "unavailable", "connection", "resourceexhausted", "toomanyrequests", "slowdown")


def is_transient(e: BaseException) -> bool:
    if isinstance(e, (ValueError, TypeError, KeyError, NotImplementedError, PermissionError)):
        return False
    name, msg = type(e).__name__.lower(), str(e).lower()
    code = str(getattr(e, "response", {}).get("Error", {}).get("Code", "") if isinstance(getattr(e, "response", None), dict) else "").lower()
    return any(k in name or k in msg or k in code for k in TRANSIENT)


def call_with_retry(fn: Callable[..., Any], *args, retries: int = 3, base_s: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                    **kwargs) -> Any:
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - cloud SDKs raise many types
            if not is_transient(e) or attempt == retries:
                raise
            sleep(base_s * (2 ** attempt) * (0.5 + random.random()))
    raise RuntimeError("unreachable")  # pragma: no cover
