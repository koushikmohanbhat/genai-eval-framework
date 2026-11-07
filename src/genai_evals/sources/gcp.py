"""Google Cloud sources: Cloud Trace (incl. spans ingested over OTLP by the Telemetry API), Cloud Logging, Vertex AI
request-response logs in BigQuery, Agent Engine Sessions, Cloud Monitoring, Gen AI evaluation service results. Read-only."""
from __future__ import annotations

from typing import Any

from ..interop.native import import_vertex_eval
from ..models import Message, Sample, Score, Usage
from .aws import _sample
from .base import Source, attr_filter, contains_value, parse_window, register
from .normalize import _content, _jl, _ns, spans_to_samples, to_span


def cloud_trace_url(project: str | None, trace_id: str | None) -> str | None:
    if not project or not trace_id or "<" in project:  # no link for unset / placeholder projects
        return None
    return f"https://console.cloud.google.com/traces/explorer;traceId={trace_id}?project={project}"


def _trace_id(trace_field: str | None) -> str | None:
    return trace_field.rsplit("/", 1)[-1] if trace_field else None


@register("gcp_cloud_trace")
class CloudTrace(Source):
    kind = "spans"

    def fetch(self):
        from datetime import datetime, timezone

        from google.cloud import trace_v1  # type: ignore
        c = self._client or trace_v1.TraceServiceClient()
        start, end = parse_window(self.spec.window)
        req = {"project_id": self.opt("project"), "view": trace_v1.ListTracesRequest.ViewType.COMPLETE,
               "start_time": datetime.fromtimestamp(start, timezone.utc), "end_time": datetime.fromtimestamp(end, timezone.utc),
               "page_size": 100}
        if self.opt("trace_filter"):
            req["filter"] = self.opt("trace_filter")
        out: list[dict[str, Any]] = []
        for t in c.list_traces(request=req):
            for sp in t.spans:
                out.append({"trace_id": t.trace_id, "spanId": str(sp.span_id), "parentSpanId": str(sp.parent_span_id) if sp.parent_span_id else None,
                            "name": sp.name, "startTime": sp.start_time.isoformat(), "endTime": sp.end_time.isoformat(), "labels": dict(sp.labels)})
            if len(out) >= int(self.opt("max_records", 20000)):
                break
        return out

    def samples(self):
        recs = self.records()
        for r in recs:
            r.setdefault("traceId", r.get("trace_id"))
        spans = [to_span(r) for r in recs]
        flt = self.spec.filter
        keep = {sp["trace_id"] for sp in spans if attr_filter(sp, flt) and (not flt.get("contains") or contains_value(sp, flt["contains"]))}
        recs = [r for r, sp in zip(recs, spans) if sp["trace_id"] in keep]
        out = spans_to_samples(recs, origin=f"source:{self.spec.name}", group_by=self.opt("group_by", "session"), target=self.spec.target or self.spec.name)
        for s in out:
            s.metadata["trace_url"] = cloud_trace_url(self.opt("project"), s.trace_id)
        return _sample(self, out)

    def trace_url(self, trace_id):
        return cloud_trace_url(self.opt("project"), trace_id)


@register("gcp_cloud_logging")
class CloudLogging(Source):
    """Cloud Logging entries correlated with traces (Agent Engine logs, GenAI log events). Produces join records."""
    kind = "records"

    def fetch(self):
        from datetime import datetime, timezone

        from google.cloud import logging as gcl  # type: ignore
        start, end = parse_window(self.spec.window)
        flt = self.opt("log_filter", "")
        flt = f'{flt} timestamp>="{datetime.fromtimestamp(start, timezone.utc).isoformat()}" timestamp<="{datetime.fromtimestamp(end, timezone.utc).isoformat()}"'
        c = self._client or gcl.Client(project=self.opt("project"))
        out = []
        for e in c.list_entries(filter_=flt.strip(), page_size=1000, max_results=int(self.opt("max_records", 5000))):
            out.append({"trace": e.trace, "spanId": e.span_id, "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                        "jsonPayload": e.payload if isinstance(e.payload, dict) else {"message": e.payload}, "labels": dict(e.labels or {})})
        return out

    def join_records(self):
        by_trace: dict[str, dict[str, Any]] = {}
        for e in self.records():
            tid = _trace_id(e.get("trace"))
            p = e.get("jsonPayload") or {}
            name = p.get("event.name") or p.get("event_name") or (e.get("labels") or {}).get("event.name") or p.get("name") or ""
            body = _jl(p.get("body", p))
            rec = by_trace.setdefault(tid or f"log-{len(by_trace)}", {"source": "cloud_logging", "trace_id": tid, "time_ns": _ns(e.get("timestamp")),
                                                                         "input_messages": [], "output_text": "", "usage": {}})
            if name in ("gen_ai.user.message", "gen_ai.system.message"):
                rec["input_messages"].append({"role": name.split(".")[1], "content": _content(body.get("content") if isinstance(body, dict) else body)})
            elif name == "gen_ai.choice":
                msg = body.get("message", body) if isinstance(body, dict) else body
                rec["output_text"] = _content(msg.get("content") if isinstance(msg, dict) else msg)
            elif isinstance(body, dict) and (body.get("gen_ai.input.messages") or body.get("gen_ai.output.messages")):
                for m in _jl(body.get("gen_ai.input.messages")) or []:
                    rec["input_messages"].append({"role": m.get("role", "user"), "content": _content(m.get("content") or m.get("parts"))})
                outs = _jl(body.get("gen_ai.output.messages")) or []
                if outs:
                    rec["output_text"] = _content(outs[-1].get("content") or outs[-1].get("parts"))
        return list(by_trace.values())


def vertex_row_to_record(row: dict[str, Any], cols: dict[str, str]) -> dict[str, Any]:
    req = _jl(row.get(cols["request"])) or {}
    resp = _jl(row.get(cols["response"])) or {}
    msgs = []
    si = req.get("systemInstruction") or req.get("system_instruction")
    if si:
        msgs.append({"role": "system", "content": _content(si.get("parts") if isinstance(si, dict) else si)})
    for c in req.get("contents", []) or []:
        msgs.append({"role": "assistant" if c.get("role") == "model" else "user", "content": _content(c.get("parts"))})
    if not msgs and req.get("messages"):        # Claude on Vertex (rawPredict) request body
        msgs = [{"role": m.get("role", "user"), "content": _content(m.get("content"))} for m in req["messages"]]
    text = ""
    if resp.get("candidates"):
        text = _content(((resp["candidates"][0] or {}).get("content") or {}).get("parts"))
    elif resp.get("content"):
        text = _content(resp["content"])
    um = resp.get("usageMetadata") or resp.get("usage_metadata") or resp.get("usage") or {}
    usage = {"input_tokens": um.get("promptTokenCount") or um.get("prompt_token_count") or um.get("input_tokens") or 0,
             "output_tokens": um.get("candidatesTokenCount") or um.get("candidates_token_count") or um.get("output_tokens") or 0}
    return {"source": "vertex_request_response", "request_id": row.get(cols.get("request_id", "request_id")), "time_ns": _ns(row.get(cols["time"])),
            "model": row.get(cols["model"]), "input_messages": msgs, "output_text": text, "usage": usage,
            "trace_id": row.get(cols.get("trace_id", "trace_id")), "session_id": row.get(cols.get("session_id", "session_id"))}


@register("gcp_vertex_request_response")
class VertexRequestResponse(Source):
    """Vertex AI request-response logging table in BigQuery. Column names are configurable (columns: {...})."""
    kind = "records"
    COLS = {"request": "full_request", "response": "full_response", "time": "logging_time", "model": "model", "request_id": "request_id"}

    @property
    def cols(self):
        return {**self.COLS, **(self.opt("columns") or {})}

    def fetch(self):
        from datetime import datetime, timezone

        from google.cloud import bigquery  # type: ignore
        table = self.opt("table", "").removeprefix("bq://")
        start, end = parse_window(self.spec.window)
        c = self._client or bigquery.Client(project=self.opt("project"))
        q = (f"SELECT * FROM `{table}` WHERE {self.cols['time']} BETWEEN @start AND @end ORDER BY {self.cols['time']} LIMIT @lim")
        job = c.query(q, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", datetime.fromtimestamp(start, timezone.utc)),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", datetime.fromtimestamp(end, timezone.utc)),
            bigquery.ScalarQueryParameter("lim", "INT64", int(self.opt("max_records", 5000)))]))
        return [dict(r) for r in job.result()]

    def join_records(self):
        return [vertex_row_to_record(r, self.cols) for r in self.records()]

    def samples(self):
        out = []
        for i, rec in enumerate(self.join_records()):
            users = [m for m in rec["input_messages"] if m["role"] == "user"]
            out.append(Sample(case_id=str(rec.get("request_id") or f"req-{i}"), target=self.spec.target or self.spec.name, origin=f"source:{self.spec.name}",
                              input=users[-1]["content"] if users else None, output=rec["output_text"], model=rec["model"], usage=Usage(**rec["usage"]),
                              messages=[Message(**m) for m in rec["input_messages"]]))
        return _sample(self, out)


@register("gcp_agent_engine_sessions")
class AgentEngineSessions(Source):
    """Vertex AI Agent Engine Sessions events -> conversation history per session."""
    kind = "history"

    @staticmethod
    def to_history(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[tuple[int, dict]]] = {}
        for ev in events:
            sid = ev.get("session_id") or ev.get("sessionId") or (ev.get("name", "").split("/sessions/")[-1].split("/")[0] if "/sessions/" in ev.get("name", "") else None)
            content = ev.get("content") or {}
            role = content.get("role") or ("user" if ev.get("author") == "user" else "model")
            text = _content(content.get("parts"))
            if not text:
                continue
            out.setdefault(sid, []).append((_ns(ev.get("timestamp")) or 0, {"role": "assistant" if role == "model" else "user", "content": text}))
        return {k: [m for _, m in sorted(v, key=lambda x: x[0])] for k, v in out.items()}

    def history(self, session_ids):
        fx = self.fixture_records()
        if fx is not None:
            return self.to_history(fx)
        import google.auth  # type: ignore
        from google.auth.transport.requests import AuthorizedSession  # type: ignore
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        http = self._client or AuthorizedSession(creds)
        loc = self.opt("location", "us-central1")
        events = []
        for sid in session_ids:
            url = f"https://{loc}-aiplatform.googleapis.com/v1beta1/{self.opt('resource')}/sessions/{sid}/events"
            r = http.get(url, timeout=30)
            if r.status_code == 200:
                for ev in r.json().get("sessionEvents", []):
                    ev["session_id"] = sid
                    events.append(ev)
        return self.to_history(events)


@register("gcp_cloud_monitoring")
class CloudMonitoring(Source):
    """Operational baselines from Vertex AI / Agent Engine metrics in Cloud Monitoring."""
    kind = "metrics"
    DEFAULT = ["aiplatform.googleapis.com/publisher/online_serving/model_invocation_count",
               "aiplatform.googleapis.com/publisher/online_serving/token_count",
               "aiplatform.googleapis.com/publisher/online_serving/model_invocation_latencies"]

    def metrics(self):
        fx = self.fixture_records()
        if fx is not None:
            return fx[0] if fx else {}
        from google.cloud import monitoring_v3  # type: ignore
        c = self._client or monitoring_v3.MetricServiceClient()
        start, end = parse_window(self.spec.window)
        interval = monitoring_v3.TimeInterval({"start_time": {"seconds": int(start)}, "end_time": {"seconds": int(end)}})
        out: dict[str, Any] = {}
        for mt in self.opt("metrics", self.DEFAULT):
            total, n = 0.0, 0
            for ts in c.list_time_series(name=f"projects/{self.opt('project')}", filter=f'metric.type = "{mt}"', interval=interval,
                                         view=monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL):
                for p in ts.points:
                    v = p.value
                    total += v.double_value or v.int64_value or (v.distribution_value.mean if v.distribution_value else 0)
                    n += 1
            out[mt.rsplit("/", 1)[-1]] = total if "count" in mt else (total / n if n else None)
        return out


@register("gcp_vertex_eval_results")
class VertexEvalResults(Source):
    """Gen AI evaluation service metrics tables (exported rows, or a BigQuery table)."""
    kind = "scores"

    def fetch(self):
        from google.cloud import bigquery  # type: ignore
        c = self._client or bigquery.Client(project=self.opt("project"))
        return [dict(r) for r in c.query(f"SELECT * FROM `{self.opt('table').removeprefix('bq://')}` LIMIT 10000").result()]

    def scores(self) -> list[Score]:
        return import_vertex_eval(self.records(), self.opt("metrics"), float(self.opt("max_score", 5.0)), self.opt("id_field", "session_id"),
                                  self.spec.target or self.spec.name)

