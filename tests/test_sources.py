import json
import shutil
from pathlib import Path

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


# --- telemetry joins: do sampled traces have their payload records? -------------------------------------------------

def test_trace_ids_come_from_request_tags():
    from genai_evals.sources.aws import bedrock_log_to_record
    from genai_evals.sources.gcp import vertex_row_to_record
    from genai_evals.sources.normalize import ids_from_tags
    assert ids_from_tags({"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "sessionId": "s1"}) \
        == ("4bf92f3577b34da6a3ce929d0e0e4736", "s1")
    assert ids_from_tags(None) == (None, None)
    rec = bedrock_log_to_record({"timestamp": "2026-11-05T09:00:00Z", "modelId": "m", "requestMetadata": {"trace_id": "t1", "session_id": "s1"},
                                 "input": {"inputBodyJson": {"messages": []}}, "output": {"outputBodyJson": {}}})
    assert (rec["trace_id"], rec["session_id"]) == ("t1", "s1")
    row = {"logging_time": "2026-11-05T09:00:00Z", "model": "g", "full_request": json.dumps({"contents": [], "labels": {"trace_id": "t2"}}),
           "full_response": "{}"}
    rec = vertex_row_to_record(row, {"request": "full_request", "response": "full_response", "time": "logging_time", "model": "model"})
    assert rec["trace_id"] == "t2"


def _join_copy(tmp_path, strip_ids: bool):
    src = Path(__file__).resolve().parents[1] / "examples/insurance"
    dst = tmp_path / "ins"
    shutil.copytree(src, dst)
    if strip_ids:
        f = dst / "fixtures/aws/bedrock_invocation_logs.jsonl"
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
        for r in rows:
            r.pop("requestMetadata", None)
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return dst / "sources.yaml"


def test_sources_check_reports_timestamp_only_joins(tmp_path, capsys):
    from genai_evals.cli import main as cli
    assert cli(["sources", "check", str(_join_copy(tmp_path / "a", strip_ids=False)), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    aws = next(x for x in rep if x["type"] == "aws_agentcore_observability")
    assert aws["joins"]["aws_bedrock_invocation_logs"]["matched"]["trace_id"] == aws["samples"] and not aws["problems"]

    assert cli(["sources", "check", str(_join_copy(tmp_path / "b", strip_ids=True))]) == 1
    out = capsys.readouterr().out
    assert "timestamp only" in out and "requestMetadata" in out


def test_diagnose_source_flags_missing_payloads_and_content():
    from genai_evals.sources.normalize import diagnose_source
    st = {"samples": 10, "records": 0, "records_with_trace_id": 0, "matched": {"trace_id": 0, "session_id": 0, "time_window": 0, "none": 10},
          "matched_by_id_share": 0.0, "time_window_share": 0.0, "unmatched_share": 1.0, "filled": 0}
    probs = diagnose_source({"source": "s", "samples": 10, "join:gcp_vertex_request_response": st,
                             "content": {"of": 10, "with_input": 10, "with_output": 2}})
    assert any("no payload records" in p and "off by default" in p for p in probs)
    assert any("2 of 10 samples have a completion" in p for p in probs)
    assert diagnose_source({"source": "s", "samples": 3, "content": {"of": 3, "with_input": 3, "with_output": 3}}) == []


def _s(i, start_s, out=None, session=None, trace=None):
    from genai_evals.models import Sample
    return Sample(case_id=f"c{i}", target="t", trace_id=trace, session_id=session, output=out,
                  metadata={"start_ns": int(start_s * 1e9), "end_ns": int((start_s + 1) * 1e9)})


def _r(rid, t_s, text, session=None, trace=None):
    return {"source": "logs", "request_id": rid, "time_ns": int(t_s * 1e9), "output_text": text,
            "input_messages": [{"role": "user", "content": "q"}], "session_id": session, "trace_id": trace}


def test_join_flags_shared_records_duplicates_and_disagreement():
    from genai_evals.sources.normalize import diagnose_source, join_stats
    # two overlapping spans, one payload record in both windows: one of the two pairings is wrong
    st = join_stats([_s(1, 100), _s(2, 100.5)], [_r("a", 101, "x"), _r("a", 101, "x")])
    assert st["duplicate_request_ids"] == 1 and st["records"] == 1
    assert st["ambiguous"] == 2 and st["shared_records"] == 1
    probs = diagnose_source({"source": "s", "samples": 2, "join:aws_bedrock_invocation_logs": st})
    assert any("share a payload record" in p for p in probs)
    # the span captured its own completion and the joined record says something else
    st = join_stats([_s(1, 100, out="Claim registered", trace="T1")], [_r("b", 100.2, "Policy lapsed", trace="T1")])
    assert st["disagreements"] == 1 and st["pairs"][0]["span_output"] == "Claim registered"
    assert any("differs from the completion captured" in p
               for p in diagnose_source({"source": "s", "samples": 1, "join:x": st}))


def test_session_join_never_takes_another_turns_record():
    from genai_evals.sources.normalize import join_stats
    s = _s(1, 100, session="S")
    st = join_stats([s], [_r("later-turn", 160, "answer to turn 2", session="S")])
    assert st["matched"]["session_id"] == 0 and st["session_outside_window"] == 1 and s.output is None
    s = _s(1, 100, session="S")
    join_stats([s], [_r("this-turn", 100.3, "answer to turn 1", session="S"), _r("later", 160, "turn 2", session="S")])
    assert s.output == "answer to turn 1"


def test_unreliable_joins_make_online_gates_invalid(tmp_path, ex_dir):
    import yaml
    from genai_evals.report.html import render_scorecard
    sy = _join_copy(tmp_path, strip_ids=True)
    r = run_online(load_suite(ex_dir / "online_checks.yaml"), load_providers(ex_dir / "providers.yaml"), load_sources(sy))
    tq = r["telemetry_quality"]
    assert tq["status"] == "unreliable" and list(tq["problems"]) == ["claims-agent-aws"]
    aws = [g for g in r["gates"] if g["target"] == "claims-agent-aws"]
    assert aws and all(g["status"] == "invalid" and "telemetry joins" in g["message"] for g in aws)
    assert all(g["status"] != "invalid" or "telemetry" not in g["message"] for g in r["gates"] if g["target"] != "claims-agent-aws")
    assert r["passed"] is False and "incomplete" in render_scorecard(r)

    cfg = yaml.safe_load(sy.read_text())
    cfg["join_policy"] = {"on_problem": "warn"}
    sy.write_text(yaml.safe_dump(cfg))
    r = run_online(load_suite(ex_dir / "online_checks.yaml"), load_providers(ex_dir / "providers.yaml"), load_sources(sy))
    assert r["telemetry_quality"]["status"] == "questionable"
    assert not any("telemetry joins" in g["message"] for g in r["gates"])
    clean = run_online(load_suite(ex_dir / "online_checks.yaml"), load_providers(ex_dir / "providers.yaml"),
                       load_sources(ex_dir / "sources.yaml"))
    assert clean["telemetry_quality"]["status"] == "ok"


def test_sources_check_trace_flag(tmp_path, capsys):
    from genai_evals.cli import main as cli
    sy = _join_copy(tmp_path, strip_ids=False)
    rec = json.loads((sy.parent / "fixtures/aws/bedrock_invocation_logs.jsonl").read_text().splitlines()[0])
    assert cli(["sources", "check", str(sy), "--pairs", "0", "--trace", rec["requestMetadata"]["trace_id"]]) == 0
    assert "matched by trace_id" in capsys.readouterr().out
    assert cli(["sources", "check", str(sy), "--pairs", "0", "--trace", "0" * 32]) == 1
    assert "in neither" in capsys.readouterr().out
