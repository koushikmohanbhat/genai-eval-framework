"""Provider-direct and neutral sources: OTLP files, OpenAI Agents SDK traces, Langfuse, OpenAI / Anthropic usage APIs."""
from __future__ import annotations

import base64
import json
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .aws import _sample
from .base import Source, parse_window, read_fixture, register
from .normalize import spans_to_samples


def _get(url: str, headers: dict[str, str], timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed provider URLs / operator-configured hosts
        return json.loads(r.read() or b"{}")


@register("otlp")
class OTLPFiles(Source):
    """OTLP JSON exports (collector file exporter, `otel-cli`, or any OTLP JSON dump). path: file or folder."""
    kind = "spans"

    def fetch(self):
        p = Path(self.opt("path", "."))
        p = p if p.is_absolute() else Path(self.base_dir) / p
        files = sorted(p.glob("*.json*")) if p.is_dir() else [p]
        out = []
        for f in files:
            out += read_fixture(f)
        return out

    def samples(self):
        return _sample(self, spans_to_samples(self.records(), origin=f"source:{self.spec.name}", group_by=self.opt("group_by", "trace"),
                                              target=self.spec.target or self.spec.name))


class GenAIEvalsTraceProcessor:
    """OpenAI Agents SDK tracing processor that appends finished spans to a JSONL file (in addition to, or instead of,
    the OpenAI traces dashboard). Register with `agents.add_trace_processor(GenAIEvalsTraceProcessor("traces.jsonl"))`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _write(self, obj: dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")

    def on_trace_start(self, trace) -> None:
        pass

    def on_trace_end(self, trace) -> None:
        pass

    def on_span_start(self, span) -> None:
        pass

    def on_span_end(self, span) -> None:
        data = span.export() if hasattr(span, "export") else dict(span)
        if data:
            self._write(data)

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


@register("openai_agents_trace")
class OpenAIAgentsTrace(Source):
    """Spans written by GenAIEvalsTraceProcessor (OpenAI Agents SDK)."""
    kind = "spans"

    def fetch(self):
        p = Path(self.opt("path", "traces.jsonl"))
        return read_fixture(p if p.is_absolute() else Path(self.base_dir) / p)

    def samples(self):
        return _sample(self, spans_to_samples(self.records(), origin=f"source:{self.spec.name}", target=self.spec.target or self.spec.name))


@register("langfuse")
class Langfuse(Source):
    kind = "spans"

    def fetch(self):
        from datetime import datetime, timezone
        host = self.opt("host") or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        auth = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
        hdr = {"Authorization": f"Basic {auth}"}
        start, end = parse_window(self.spec.window)
        q = {"fromStartTime": datetime.fromtimestamp(start, timezone.utc).isoformat(), "toStartTime": datetime.fromtimestamp(end, timezone.utc).isoformat(),
             "limit": 100, "page": 1}
        if self.spec.filter.get("name"):
            q["name"] = self.spec.filter["name"]
        out: list[dict[str, Any]] = []
        while len(out) < int(self.opt("max_records", 5000)):
            data = _get(f"{host}/api/public/observations?{urllib.parse.urlencode(q)}", hdr)
            out += data.get("data", [])
            if q["page"] >= (data.get("meta") or {}).get("totalPages", 1):
                break
            q["page"] += 1
        return out

    def samples(self):
        return _sample(self, spans_to_samples(self.records(), origin=f"source:{self.spec.name}", target=self.spec.target or self.spec.name))


@register("openai_usage")
class OpenAIUsage(Source):
    """Organization Usage API (admin key in OPENAI_ADMIN_KEY): token totals by model, for cost reconciliation."""
    kind = "metrics"

    def metrics(self):
        fx = self.fixture_records()
        if fx is None:
            start, _ = parse_window(self.spec.window)
            key = os.environ[self.opt("admin_key_env", "OPENAI_ADMIN_KEY")]
            data = _get(f"https://api.openai.com/v1/organization/usage/completions?start_time={int(start)}&bucket_width=1d&group_by=model",
                        {"Authorization": f"Bearer {key}"})
            fx = data.get("data", [])
        totals: dict[str, dict[str, int]] = {}
        for b in fx:
            for r in b.get("results", []):
                m = totals.setdefault(r.get("model") or "all", {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "requests": 0})
                m["input_tokens"] += r.get("input_tokens", 0) or 0
                m["output_tokens"] += r.get("output_tokens", 0) or 0
                m["cached_tokens"] += r.get("input_cached_tokens", 0) or 0
                m["requests"] += r.get("num_model_requests", 0) or 0
        return {"openai_usage_by_model": totals}


@register("anthropic_usage")
class AnthropicUsage(Source):
    """Usage and Cost Admin API (admin key in ANTHROPIC_ADMIN_KEY): token totals by model."""
    kind = "metrics"

    def metrics(self):
        fx = self.fixture_records()
        if fx is None:
            from datetime import datetime, timezone
            start, _ = parse_window(self.spec.window)
            key = os.environ[self.opt("admin_key_env", "ANTHROPIC_ADMIN_KEY")]
            ts = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = _get(f"https://api.anthropic.com/v1/organizations/usage_report/messages?starting_at={ts}&bucket_width=1d&group_by[]=model",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"})
            fx = data.get("data", [])
        totals: dict[str, dict[str, int]] = {}
        for b in fx:
            for r in b.get("results", []):
                m = totals.setdefault(r.get("model") or "all", {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0})
                m["input_tokens"] += (r.get("uncached_input_tokens", 0) or 0) + (r.get("cache_read_input_tokens", 0) or 0)
                m["output_tokens"] += r.get("output_tokens", 0) or 0
                m["cached_tokens"] += r.get("cache_read_input_tokens", 0) or 0
        return {"anthropic_usage_by_model": totals}
