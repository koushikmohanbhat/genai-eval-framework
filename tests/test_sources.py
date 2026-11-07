import json

from genai_evals.config import load_providers, load_sources, load_suite
from genai_evals.online import run_online
from genai_evals.sources import attach_history, build_source, enrich, spans_to_samples
from genai_evals.sources.normalize import to_span


def test_otlp_json_with_openinference_and_new_semconv():
    doc = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
                              "scopeSpans": [{"spans": [
                                  {"traceId": "a" * 32, "spanId": "1", "name": "agent", "startTimeUnixNano": "1700000000000000000", "endTimeUnixNano": "1700000002000000000",
                                   "attributes": [{"key": "openinference.span.kind", "value": {"stringValue": "AGENT"}}, {"key": "session.id", "value": {"stringValue": "s1"}}]},
                                  {"traceId": "a" * 32, "spanId": "2", "parentSpanId": "1", "name": "llm", "startTimeUnixNano": "1700000000100000000", "endTimeUnixNano": "1700000000900000000",
                                   "attributes": [{"key": "openinference.span.kind", "value": {"stringValue": "LLM"}},
                                                  {"key": "llm.input_messages.0.message.role", "value": {"stringValue": "user"}},
                                                  {"key": "llm.input_messages.0.message.content", "value": {"stringValue": "Is flood covered?"}},
                                                  {"key": "llm.output_messages.0.message.role", "value": {"stringValue": "assistant"}},
                                                  {"key": "llm.output_messages.0.message.content", "value": {"stringValue": "Yes [pw-06]"}},
                                                  {"key": "llm.token_count.prompt", "value": {"intValue": "40"}}]},
                                  {"traceId": "a" * 32, "spanId": "3", "parentSpanId": "1", "name": "retrieve", "startTimeUnixNano": "1700000000000000000",
                                   "attributes": [{"key": "openinference.span.kind", "value": {"stringValue": "RETRIEVER"}},
                                                  {"key": "retrieval.documents.0.document.id", "value": {"stringValue": "pw-06"}},
                                                  {"key": "retrieval.documents.0.document.content", "value": {"stringValue": "Flood damage is covered."}}]},
                                  {"traceId": "a" * 32, "spanId": "4", "parentSpanId": "1", "name": "execute_tool search",
                                   "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                                                  {"key": "gen_ai.tool.name", "value": {"stringValue": "search"}},
                                                  {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "{\"q\": \"flood\"}"}}]}]}]}]}
    [s] = spans_to_samples([doc], origin="otlp")
    assert s.input == "Is flood covered?" and s.output == "Yes [pw-06]" and s.usage.input_tokens == 40
    assert s.context[0].id == "pw-06" and s.trajectory[0].args == {"q": "flood"} and s.session_id == "s1" and s.latency_ms == 2000
    assert s.target == "svc"


def test_span_shapes_xray_cloud_trace_openai_langfuse():
    xr = to_span({"id": "seg1", "trace_id": "1-5f84c7a1-aaaaaaaaaaaaaaaaaaaaaaaa", "name": "claims", "start_time": 1700000000.0, "end_time": 1700000001.5,
                  "annotations": {"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 5}})
    assert xr["start_ns"] == 1700000000 * 10**9 and xr["attrs"]["gen_ai.operation.name"] == "chat"
    ct = to_span({"trace_id": "t", "spanId": "9", "name": "call_llm", "startTime": "2026-11-05T09:00:00+00:00", "endTime": "2026-11-05T09:00:01+00:00",
                  "labels": {"gen_ai.request.model": "gemini"}})
    assert ct["attrs"]["gen_ai.request.model"] == "gemini" and ct["end_ns"] - ct["start_ns"] == 10**9
    oa = [{"object": "trace.span", "id": "s1", "trace_id": "trace_x", "parent_id": None, "started_at": "2026-11-05T09:00:00Z", "ended_at": "2026-11-05T09:00:02Z",
           "span_data": {"type": "agent", "name": "triage"}},
          {"object": "trace.span", "id": "s2", "trace_id": "trace_x", "parent_id": "s1", "started_at": "2026-11-05T09:00:00Z", "ended_at": "2026-11-05T09:00:01Z",
           "span_data": {"type": "function", "name": "lookup_policy", "input": "{\"policy_number\": \"X\"}", "output": "{\"status\": \"active\"}"}},
          {"object": "trace.span", "id": "s3", "trace_id": "trace_x", "parent_id": "s1", "started_at": "2026-11-05T09:00:01Z", "ended_at": "2026-11-05T09:00:02Z",
           "span_data": {"type": "handoff", "from_agent": "triage", "to_agent": "claims"}},
          {"object": "trace.span", "id": "s4", "trace_id": "trace_x", "parent_id": "s1", "started_at": "2026-11-05T09:00:01Z", "ended_at": "2026-11-05T09:00:02Z",
           "span_data": {"type": "response", "model": "m", "input": [{"role": "user", "content": "hi"}], "output": [{"role": "assistant", "content": "hello"}],
                         "usage": {"input_tokens": 9, "output_tokens": 2}}}]
    [s] = spans_to_samples(oa)
    assert [t.name for t in s.trajectory] == ["lookup_policy", "handoff:claims"] and s.output == "hello" and s.usage.total == 11
    lf = [{"id": "o1", "traceId": "lt", "type": "GENERATION", "name": "gen", "startTime": "2026-11-05T09:00:00Z", "endTime": "2026-11-05T09:00:01Z",
           "model": "gpt", "input": [{"role": "user", "content": "q?"}], "output": {"role": "assistant", "content": "a."}, "usage": {"input": 3, "output": 1},
           "promptName": "policy_qa", "promptVersion": 3}]
    [s2] = spans_to_samples(lf)
    assert s2.output == "a." and s2.prompt_versions == {"policy_qa": "3"}


def test_enrich_by_time_window_and_history():
    [s] = spans_to_samples([{"traceId": "t1", "spanId": "1", "name": "invoke_agent", "startTimeUnixNano": 1_700_000_000_000_000_000,
                             "endTimeUnixNano": 1_700_000_003_000_000_000, "attributes": {"session.id": "s9"}}])
    assert not s.output
    n = enrich([s], [{"time_ns": 1_700_000_002_000_000_000, "input_messages": [{"role": "user", "content": "q"}], "output_text": "a",
                      "usage": {"input_tokens": 3, "output_tokens": 1}, "source": "logs"}])
    assert n == 1 and s.input == "q" and s.output == "a" and s.usage.total == 4
    assert attach_history([s], {"s9": [{"role": "user", "content": "q"}]}) == 1 and s.metadata["history"]


def test_agentcore_source_live_fetch_with_fake_logs_client(ex_dir):
    lines = [json.loads(line) for line in (ex_dir / "fixtures/aws/aws_spans.jsonl").read_text().splitlines()]

    class Logs:
        def __init__(self):
            self.kw = []

        def filter_log_events(self, **kw):
            self.kw.append(kw)
            half = len(lines) // 2
            if "nextToken" not in kw:
                return {"events": lines[:half], "nextToken": "n"}
            return {"events": lines[half:]}
    logs = Logs()
    src = build_source({"name": "aws", "type": "aws_agentcore_observability", "region": "ap-south-1", "filter": {"service_name": "claims-agent"}}, ".", logs)
    samples = src.samples()
    assert len(samples) == 8 and logs.kw[0]["logGroupName"] == "aws/spans" and logs.kw[0]["filterPattern"] == '"claims-agent"'
    assert samples[0].metadata["trace_url"].startswith("https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#xray:traces/1-")


def test_online_end_to_end_over_both_clouds(ex_dir):
    r = run_online(load_suite(ex_dir / "online_checks.yaml"), load_providers(ex_dir / "providers.yaml"), load_sources(ex_dir / "sources.yaml"))
    by = {s["target"]: [] for s in r["samples"]}
    for s in r["samples"]:
        by[s["target"]].append(s)
    assert len(by["claims-agent-aws"]) == 8 and len(by["claims-agent-gcp"]) == 5
    assert all(s["output"] for s in by["claims-agent-gcp"])                       # recovered from BigQuery request-response logs
    assert any("enriched_from" in s["metadata"] for s in by["claims-agent-aws"])  # content capture off -> Bedrock invocation logs
    aws = r["summary"]["claims-agent-aws"]
    assert aws["graders"]["no_pii_echo"]["mean"] < 1 and aws["graders"]["no_payout_promise"]["mean"] < 1
    assert "agentcore:Builtin.Helpfulness" in aws["graders"]
    assert "prompt:claims_agent@1.2.0" in aws["slices"]
    assert r["operational"]["claims-agent-aws:aws_cloudwatch_metrics"]["Invocations"] == 1874
    assert not r["passed"]


def test_online_report_is_framed_as_monitoring_with_population(ex_dir):
    from genai_evals.report.html import render_report, render_scorecard
    r = run_online(load_suite(ex_dir / "online_checks.yaml"), load_providers(ex_dir / "providers.yaml"), load_sources(ex_dir / "sources.yaml"))
    logged = [s for s in r["sources"] if "samples" in s]
    assert logged and all("population" in s and s["population"] >= s["samples"] for s in logged)
    sc, rep = render_scorecard(r), render_report(r)
    assert "Production monitoring" in sc and "Is it ready?" not in sc and "not ground-truth" in rep
    assert "structured identifiers only" in rep


def test_sampling_is_deterministic_by_session():
    from genai_evals.config import SourceSpec
    from genai_evals.models import Sample
    from genai_evals.sources.aws import _sample
    from genai_evals.sources.base import Source

    spec = SourceSpec(name="s", type="otlp_file", sampling=0.5)
    samples = [Sample(case_id=f"c{i}", target="t", input="", output="", session_id=f"sess{i % 20}") for i in range(200)]
    a = _sample(Source(spec, "."), samples)
    b = _sample(Source(spec, "."), list(reversed(samples)))
    assert {s.case_id for s in a} == {s.case_id for s in b}
    kept = {s.session_id for s in a}
    assert all((s.session_id in kept) == (s in a) for s in samples)          # whole sessions in or out
    assert 0 < len(kept) < 20
