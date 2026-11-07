"""AWS sources: AgentCore Observability spans (CloudWatch aws/spans), X-Ray, Bedrock model invocation logs,
AgentCore Memory, CloudWatch metrics, AgentCore Evaluations results. All read-only."""
from __future__ import annotations

import gzip
import json
from typing import Any

from ..interop.native import import_agentcore_results
from ..models import Sample, Score
from .base import Source, attr_filter, contains_value, parse_window, register
from .normalize import _content, _ns, spans_to_samples, to_span


def _aws_client(src: Source, service: str):
    """The injected client (tests, conformance kit, custom sessions), or a boto3 client for the source's region."""
    if src._client is not None:
        return src._client
    import boto3
    return boto3.client(service, region_name=src.opt("region"))


def _logs_client(src: Source):
    if src._client is None:
        import boto3
        src._client = boto3.client("logs", region_name=src.opt("region"))
    return src._client


def filter_log_events(src: Source, log_group: str, pattern: str | None = None, max_records: int = 5000) -> list[dict[str, Any]]:
    start, end = parse_window(src.spec.window)
    c = _logs_client(src)
    kw: dict[str, Any] = {"logGroupName": log_group, "startTime": int(start * 1000), "endTime": int(end * 1000)}
    if pattern:
        kw["filterPattern"] = pattern
    if src.opt("log_stream_prefix"):
        kw["logStreamNamePrefix"] = src.opt("log_stream_prefix")
    out: list[dict[str, Any]] = []
    while len(out) < max_records:
        r = src.call(c.filter_log_events, **kw)
        for e in r.get("events", []):
            try:
                out.append(json.loads(e["message"]))
            except (json.JSONDecodeError, KeyError):
                continue
        if not r.get("nextToken"):
            break
        kw["nextToken"] = r["nextToken"]
    return out


def xray_url(region: str | None, trace_id: str | None) -> str | None:
    if not trace_id or not region or "<" in region:  # no link for unset / placeholder regions
        return None
    h = trace_id.replace("-", "")
    xid = trace_id if trace_id.startswith("1-") else (f"1-{h[:8]}-{h[8:]}" if len(h) == 32 else trace_id)
    return f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#xray:traces/{xid}"


def _sample(src: Source, samples: list[Sample]) -> list[Sample]:
    """Deterministic, unit-consistent sampling: a session (or trace) is in or out based on a hash of its id, so the
    same session is always treated the same way across runs and joins, and sampling does not depend on arrival order.
    The population size is kept on the source so reports can say "n of N"."""
    import hashlib
    rate = float(src.spec.sampling or 1.0)
    src.population = len(samples)
    if rate < 1.0:
        def keep(s: Sample) -> bool:
            unit = s.session_id or s.trace_id or s.case_id
            h = int(hashlib.sha256(f"{src.opt('seed', 7)}:{unit}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            return h < rate
        samples = [s for s in samples if keep(s)]
    return samples


@register("aws_agentcore_observability")
class AgentCoreObservability(Source):
    """OTel spans in CloudWatch Logs (default log group aws/spans), as written by AgentCore Runtime / ADOT."""
    kind = "spans"

    def fetch(self):
        flt = self.spec.filter
        pattern = None
        if flt.get("service_name"):
            pattern = f'"{flt["service_name"]}"'
        return filter_log_events(self, self.opt("log_group", "aws/spans"), pattern, int(self.opt("max_records", 5000)))

    def samples(self) -> list[Sample]:
        recs = self.records()
        spans = [to_span(r) for r in recs]
        flt = self.spec.filter
        keep_traces = {sp["trace_id"] for sp in spans if attr_filter(sp, flt) and (not flt.get("agent_arn") or contains_value(sp, flt["agent_arn"]))}
        recs = [r for r, sp in zip(recs, spans) if sp["trace_id"] in keep_traces]
        out = spans_to_samples(recs, origin=f"source:{self.spec.name}", group_by=self.opt("group_by", "session"), target=self.spec.target or self.spec.name)
        for s in out:
            s.metadata["trace_url"] = xray_url(self.opt("region"), s.trace_id)
        return _sample(self, out)

    def trace_url(self, trace_id):
        return xray_url(self.opt("region"), trace_id)


def _flatten_segment(doc: dict[str, Any], trace_id: str, parent: str | None = None) -> list[dict[str, Any]]:
    seg = {**doc, "trace_id": trace_id, "parent_id": parent or doc.get("parent_id")}
    out = [seg]
    for sub in doc.get("subsegments", []) or []:
        out += _flatten_segment(sub, trace_id, doc["id"])
    return out


@register("aws_xray")
class XRay(Source):
    kind = "spans"

    def fetch(self):
        from datetime import datetime, timezone

        c = _aws_client(self, "xray")
        start, end = parse_window(self.spec.window)
        kw: dict[str, Any] = {"StartTime": datetime.fromtimestamp(start, timezone.utc), "EndTime": datetime.fromtimestamp(end, timezone.utc)}
        if self.opt("filter_expression"):
            kw["FilterExpression"] = self.opt("filter_expression")
        ids: list[str] = []
        while True:
            r = self.call(c.get_trace_summaries, **kw)
            ids += [t["Id"] for t in r.get("TraceSummaries", [])]
            if not r.get("NextToken") or len(ids) >= int(self.opt("max_traces", 500)):
                break
            kw["NextToken"] = r["NextToken"]
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 5):
            for t in c.batch_get_traces(TraceIds=ids[i:i + 5]).get("Traces", []):
                for seg in t.get("Segments", []):
                    out += _flatten_segment(json.loads(seg["Document"]), t["Id"])
        return out

    def samples(self):
        recs = self.records()
        flat = []
        for r in recs:
            flat += _flatten_segment(r, r.get("trace_id"), r.get("parent_id")) if "subsegments" in r else [r]
        out = spans_to_samples(flat, origin=f"source:{self.spec.name}", target=self.spec.target or self.spec.name)
        for s in out:
            s.metadata["trace_url"] = xray_url(self.opt("region"), s.trace_id)
        return _sample(self, out)


def bedrock_log_to_record(r: dict[str, Any]) -> dict[str, Any]:
    """Bedrock model invocation log (Converse / InvokeModel) -> join record."""
    inp = (r.get("input") or {}).get("inputBodyJson") or {}
    out = (r.get("output") or {}).get("outputBodyJson") or {}
    msgs = []
    for s in inp.get("system", []) if isinstance(inp.get("system"), list) else ([{"text": inp["system"]}] if inp.get("system") else []):
        msgs.append({"role": "system", "content": _content(s)})
    for m in inp.get("messages", []) or []:
        msgs.append({"role": m.get("role", "user"), "content": _content(m.get("content"))})
    if not msgs and inp.get("inputText"):
        msgs.append({"role": "user", "content": inp["inputText"]})
    text = ""
    if isinstance(out.get("output"), dict):
        text = _content((out["output"].get("message") or {}).get("content"))
    elif out.get("content"):
        text = _content(out["content"])
    elif out.get("results"):
        text = _content(out["results"][0].get("outputText"))
    u = out.get("usage") or {}
    usage = {"input_tokens": (r.get("input") or {}).get("inputTokenCount") or u.get("inputTokens") or u.get("input_tokens") or 0,
             "output_tokens": (r.get("output") or {}).get("outputTokenCount") or u.get("outputTokens") or u.get("output_tokens") or 0}
    rec = {"source": "bedrock_invocation_logs", "request_id": r.get("requestId"), "time_ns": _ns(r.get("timestamp")), "model": r.get("modelId"),
           "input_messages": msgs, "output_text": text, "usage": usage, "trace_id": (r.get("trace") or {}).get("traceId") if isinstance(r.get("trace"), dict) else None}
    if out.get("stopReason") == "guardrail_intervened" or (out.get("amazon-bedrock-guardrailAction") == "INTERVENED"):
        rec["guardrail"] = {"action": "INTERVENED", "source": "bedrock_guardrails"}
    return rec


@register("aws_bedrock_invocation_logs")
class BedrockInvocationLogs(Source):
    """Bedrock model invocation logging, from CloudWatch Logs (log_group) or S3 (s3_uri)."""
    kind = "records"

    def fetch(self):
        if self.opt("s3_uri"):
            bucket, _, prefix = self.opt("s3_uri").removeprefix("s3://").partition("/")
            s3 = _aws_client(self, "s3")
            out = []
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", [])[: int(self.opt("max_objects", 200))]:
                    body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    if obj["Key"].endswith(".gz"):
                        body = gzip.decompress(body)
                    out += [json.loads(line) for line in body.decode().splitlines() if line.strip()]
            return out
        return filter_log_events(self, self.opt("log_group", "/aws/bedrock/modelinvocations"), None, int(self.opt("max_records", 5000)))

    def join_records(self):
        return [bedrock_log_to_record(r) for r in self.records()]

    def samples(self):
        """Used when invocation logs are the primary source (plain model apps without tracing): one sample per invocation."""
        out = []
        for i, rec in enumerate(self.join_records()):
            users = [m for m in rec["input_messages"] if m["role"] == "user"]
            from ..models import Message, Usage
            s = Sample(case_id=rec.get("request_id") or f"inv-{i}", target=self.spec.target or self.spec.name, origin=f"source:{self.spec.name}",
                       input=users[-1]["content"] if users else None, output=rec["output_text"], model=rec["model"],
                       usage=Usage(**rec["usage"]), messages=[Message(**m) for m in rec["input_messages"]])
            if rec.get("guardrail"):
                s.metadata["guardrail"] = rec["guardrail"]
            out.append(s)
        return _sample(self, out)


@register("aws_agentcore_memory")
class AgentCoreMemory(Source):
    """AgentCore Memory short-term events per session -> conversation history for multi-turn grading."""
    kind = "history"

    def _events_to_history(self, events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        hist: dict[str, list[tuple[int, dict]]] = {}
        for ev in events:
            sid = ev.get("sessionId")
            for p in ev.get("payload", []) or []:
                conv = p.get("conversational")
                if not conv:
                    continue
                role = str(conv.get("role", "USER")).lower()
                role = "assistant" if role in ("assistant", "agent", "model") else "tool" if role == "tool" else "user"
                hist.setdefault(sid, []).append((_ns(ev.get("eventTimestamp")) or 0, {"role": role, "content": _content(conv.get("content"))}))
        return {k: [m for _, m in sorted(v, key=lambda x: x[0])] for k, v in hist.items()}

    def history(self, session_ids):
        fx = self.fixture_records()
        if fx is not None:
            return self._events_to_history(fx)
        c = _aws_client(self, "bedrock-agentcore")
        events = []
        for sid in session_ids:
            kw = {"memoryId": self.opt("memory_id"), "sessionId": sid, "actorId": self.opt("actor_id", "default"), "includePayloads": True}
            while True:
                r = self.call(c.list_events, **kw)
                events += r.get("events", [])
                if not r.get("nextToken"):
                    break
                kw["nextToken"] = r["nextToken"]
        return self._events_to_history(events)


@register("aws_cloudwatch_metrics")
class CloudWatchMetrics(Source):
    """Operational baselines from AWS/Bedrock (and AgentCore) metrics: invocations, latency, tokens, throttles."""
    kind = "metrics"
    DEFAULT = [("AWS/Bedrock", "Invocations", "Sum"), ("AWS/Bedrock", "InvocationLatency", "Average"), ("AWS/Bedrock", "InputTokenCount", "Sum"),
               ("AWS/Bedrock", "OutputTokenCount", "Sum"), ("AWS/Bedrock", "InvocationThrottles", "Sum"), ("AWS/Bedrock", "InvocationClientErrors", "Sum")]

    def metrics(self):
        fx = self.fixture_records()
        if fx is not None:
            return fx[0] if fx else {}
        from datetime import datetime, timezone

        c = _aws_client(self, "cloudwatch")
        start, end = parse_window(self.spec.window)
        dims = [{"Name": k, "Value": v} for k, v in (self.opt("dimensions") or {}).items()]
        queries = []
        for i, (ns, name, stat) in enumerate([tuple(x) for x in self.opt("metrics", self.DEFAULT)]):
            queries.append({"Id": f"m{i}", "Label": name, "MetricStat": {"Metric": {"Namespace": ns, "MetricName": name, "Dimensions": dims},
                                                                          "Period": int(end - start), "Stat": stat}})
        r = c.get_metric_data(MetricDataQueries=queries, StartTime=datetime.fromtimestamp(start, timezone.utc), EndTime=datetime.fromtimestamp(end, timezone.utc))
        return {m["Label"]: (m["Values"][0] if m.get("Values") else None) for m in r.get("MetricDataResults", [])}


@register("aws_agentcore_evaluations")
class AgentCoreEvaluationsResults(Source):
    """Results written by AgentCore Evaluations (online or on-demand) to its results log group."""
    kind = "scores"

    def fetch(self):
        return filter_log_events(self, self.opt("log_group"), None, int(self.opt("max_records", 5000)))

    def scores(self) -> list[Score]:
        sc = import_agentcore_results(self.records(), key_by=self.opt("key_by", "session"))
        for s in sc:
            s.target = self.spec.target or s.target
        return sc
