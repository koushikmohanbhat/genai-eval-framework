"""Conformance specs for the built-in adapters (run in CI by tests/test_conformance.py)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

from . import ProviderConformance, SourceConformance, ThrottlingException


# ---- fake cloud clients for the live code path ---------------------------------------
class FakeLogs:
    """CloudWatch Logs `filter_log_events` with nextToken paging; raises a throttling error `failures` times first."""

    def __init__(self, records: list[dict[str, Any]], page_size: int, failures: int):
        self.events = [r if isinstance(r.get("message"), str) else {"timestamp": 0, "message": json.dumps(r)} for r in records]
        self.page, self.failures, self.calls = page_size, failures, []

    def filter_log_events(self, **kw):
        self.calls.append(kw)
        if self.failures:
            self.failures -= 1
            raise ThrottlingException("ThrottlingException: Rate exceeded")
        assert "startTime" in kw and "endTime" in kw, "live fetch must pass the time window"
        i = int(kw.get("nextToken", 0))
        out = {"events": self.events[i:i + self.page]}
        if i + self.page < len(self.events):
            out["nextToken"] = str(i + self.page)
        return out


class FakeMemory:
    """AgentCore Memory `list_events` per session with nextToken paging."""

    def __init__(self, records, page_size, failures):
        self.records, self.page, self.failures = records, page_size, failures

    def list_events(self, **kw):
        if self.failures:
            self.failures -= 1
            raise ThrottlingException("ThrottlingException")
        evs = [r for r in self.records if r.get("sessionId") == kw["sessionId"]]
        i = int(kw.get("nextToken", 0))
        out = {"events": evs[i:i + self.page]}
        if i + self.page < len(evs):
            out["nextToken"] = str(i + self.page)
        return out


SPECS: list[Any] = [
    SourceConformance("aws_agentcore_observability", "aws_agentcore_observability.jsonl", {"region": "ap-south-1"},
                      expect_fields=["output", "trajectory", "session_id", "trace_id", "latency_ms"], client_factory=FakeLogs),
    SourceConformance("aws_bedrock_invocation_logs", "aws_bedrock_invocation_logs.jsonl", {"region": "ap-south-1"}, client_factory=FakeLogs),
    SourceConformance("aws_agentcore_evaluations", "aws_agentcore_evaluations.jsonl", {"log_group": "/aws/bedrock-agentcore/evaluations/results"},
                      client_factory=FakeLogs),
    SourceConformance("aws_agentcore_memory", "aws_agentcore_memory.jsonl", {"memory_id": "mem-1", "region": "ap-south-1"}, client_factory=FakeMemory),
    SourceConformance("aws_xray", "aws_xray.jsonl", {"region": "ap-south-1"}, expect_fields=["trace_id", "latency_ms"]),
    SourceConformance("aws_cloudwatch_metrics", "aws_cloudwatch_metrics.json", {"region": "ap-south-1"}),
    SourceConformance("gcp_cloud_trace", "gcp_cloud_trace.jsonl", {"project": "p"}, expect_fields=["trace_id", "session_id", "trajectory"]),
    SourceConformance("gcp_cloud_logging", "gcp_cloud_logging.jsonl", {"project": "p"}),
    SourceConformance("gcp_vertex_request_response", "gcp_vertex_request_response.jsonl", {"table": "bq://p.d.t"}),
    SourceConformance("gcp_agent_engine_sessions", "gcp_agent_engine_sessions.jsonl", {"resource": "projects/p/locations/l/reasoningEngines/1"}),
    SourceConformance("gcp_cloud_monitoring", "gcp_cloud_monitoring.json", {"project": "p"}),
    SourceConformance("otlp", "otlp.json", expect_fields=["input", "output", "context", "trajectory", "usage", "latency_ms"]),
    SourceConformance("openai_agents_trace", "openai_agents_trace.jsonl", expect_fields=["output", "trajectory", "usage"]),
    SourceConformance("langfuse", "langfuse.jsonl", expect_fields=["output", "prompt_versions"]),
]


# ---- providers: fake SDK clients with one canned response (text + one tool call) ------------
def _rec(ret):
    return lambda **kw: ret


def _openai():
    return NS(responses=NS(create=_rec({"id": "r", "model": "m", "output": [
        {"type": "function_call", "call_id": "c2", "name": "lookup", "arguments": '{"id":"Y"}'},
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {"input_tokens": 10, "output_tokens": 5}})))


def _anthropic():
    return NS(messages=NS(create=_rec(NS(content=[NS(type="text", text="ok"), NS(type="tool_use", id="t1", name="lookup", input={"id": "Z"})],
                                         usage=NS(input_tokens=7, output_tokens=3, cache_read_input_tokens=0), model="claude", id="m1",
                                         stop_reason="tool_use"))))


def _bedrock():
    return NS(converse=_rec({"output": {"message": {"content": [{"text": "ok"}, {"toolUse": {"toolUseId": "u1", "name": "lookup", "input": {"id": "Q"}}}]}},
                             "usage": {"inputTokens": 11, "outputTokens": 6}, "metrics": {"latencyMs": 321}, "stopReason": "tool_use"}))


def _vertex():
    return NS(models=NS(generate_content=_rec({"candidates": [{"content": {"parts": [{"text": "ok"}, {"function_call": {"name": "lookup", "args": {"id": "G"}}}]},
                                                               "finish_reason": "STOP"}],
                                               "usage_metadata": {"prompt_token_count": 12, "candidates_token_count": 4}})))


def _compat():
    msg = NS(content="ok", tool_calls=[NS(id="k", function=NS(name="lookup", arguments='{"id":"W"}'))])
    return NS(chat=NS(completions=NS(create=_rec(NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=3, completion_tokens=2), model="llama")))))


SPECS += [
    ProviderConformance("openai", {"model": "m"}, _openai, "ok", "lookup"),
    ProviderConformance("anthropic", {"model": "claude"}, _anthropic, "ok", "lookup"),
    ProviderConformance("vertex_anthropic", {"model": "claude", "project": "p", "location": "us-east5"}, _anthropic, "ok", "lookup"),
    ProviderConformance("bedrock", {"model": "anthropic.claude-x", "region": "ap-south-1"}, _bedrock, "ok", "lookup"),
    ProviderConformance("bedrock_anthropic", {"model": "anthropic.claude-x", "region": "ap-south-1"}, _bedrock, "ok", "lookup"),
    ProviderConformance("vertex", {"model": "gemini", "project": "p", "location": "asia-south1"}, _vertex, "ok", "lookup"),
    ProviderConformance("openai_compatible", {"model": "llama", "base_url": "http://gw/v1"}, _compat, "ok", "lookup"),
]
