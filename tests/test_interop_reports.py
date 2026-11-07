import json

import yaml

from genai_evals.cli import main as cli
from genai_evals.config import load_providers, load_suite
from genai_evals.interop import export_config, import_agentcore_results, import_results, import_vertex_eval, package_agentcore_evaluator
from genai_evals.publish import publish_cloudwatch, to_emf
from genai_evals.report import junit_xml, markdown_summary, render_report
from genai_evals.runner import Runner


def _result(ex_dir):
    return Runner(load_suite(ex_dir / "claims_extraction.yaml"), load_providers(ex_dir / "providers.yaml"), use_cache=False).run()


def test_promptfoo_export_live_suite(ex_dir):
    cfg, notes = export_config(load_suite(ex_dir / "claims_extraction.live.yaml"), load_providers(ex_dir / "providers.live.yaml"))
    ids = {p["label"]: p["id"] for p in cfg["providers"]}
    assert ids["openai"].startswith("openai:responses:") and ids["bedrock-nova"].startswith("bedrock:") and ids["vertex-gemini"].startswith("vertex:")
    assert cfg["providers"][0]["config"]["store"] is False
    assert {a["type"] for a in cfg["defaultTest"]["assert"]} >= {"is-json", "latency", "cost"}
    assert len(cfg["tests"]) == 24 and any("field_match" in n for n in notes)
    yaml.safe_dump(cfg)


def test_promptfoo_import(tmp_path):
    res = {"results": {"version": 3, "results": [
        {"provider": {"id": "openai:gpt", "label": "openai"}, "testCase": {"description": "c1"}, "vars": {"input": "q"},
         "response": {"output": "a", "tokenUsage": {"prompt": 10, "completion": 2}}, "latencyMs": 120, "cost": 0.001,
         "gradingResult": {"pass": False, "componentResults": [{"pass": True, "score": 1, "assertion": {"type": "icontains", "metric": "contains"}},
                                                               {"pass": False, "score": 0.2, "reason": "off", "assertion": {"type": "llm-rubric"}}]}}]}}
    p = tmp_path / "pf.json"
    p.write_text(json.dumps(res))
    samples, scores = import_results(p)
    assert samples[0].target == "openai" and samples[0].usage.total == 12
    assert {s.grader: s.passed for s in scores} == {"contains": True, "llm-rubric": False}
    rc = cli(["promptfoo", "import", str(p), "--out", str(tmp_path / "out"), "--no-html"])
    assert rc == 0 and (tmp_path / "out" / "result.json").exists()


def test_native_imports():
    sc = import_agentcore_results([{"evaluatorName": "Builtin.Helpfulness", "sessionId": "s1", "value": 0.8, "explanation": "ok"},
                                   {"evaluatorId": "arn:x/Custom.Tone", "sessionId": "s2", "label": "PASS"}])
    assert sc[0].grader == "agentcore:Builtin.Helpfulness" and sc[0].value == 0.8 and sc[1].value == 1.0
    vx = import_vertex_eval([{"id": "c1", "groundedness/score": 4, "groundedness/explanation": "fine"}])
    assert vx[0].grader == "vertex:groundedness" and vx[0].value == 0.8


def test_agentcore_evaluator_package_rejects_model_graders(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        package_agentcore_evaluator({"name": "r", "type": "rubric"}, tmp_path)
    out = package_agentcore_evaluator({"name": "pii", "type": "pii_leak"}, tmp_path / "pkg")
    assert (out / "handler.py").exists()


def test_reports_escape_untrusted_output(ex_dir):
    r = _result(ex_dir)
    r["samples"][0]["output"] = "<script>alert(1)</script>"
    html = render_report(r)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    md = markdown_summary(r)
    assert "extractor-v2" in md and "Gates" in md
    xml = junit_xml(r)
    assert xml.startswith("<?xml") and 'failures="' in xml


def test_emf_and_cloudwatch_publish(ex_dir):
    r = _result(ex_dir)
    lines = [json.loads(x) for x in to_emf(r)]
    assert lines[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "GenAIEvals" and "Score" in lines[0]

    class CW:
        calls = []

        def put_metric_data(self, **kw):
            self.calls.append(kw)
    n = publish_cloudwatch(r, client=CW())
    assert n > 0 and CW.calls[0]["Namespace"] == "GenAIEvals"


def test_otel_telemetry_links_source_traces(ex_dir):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from genai_evals.otel import Telemetry
    exp, reader = InMemorySpanExporter(), InMemoryMetricReader()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    t = Telemetry(tp, MeterProvider(metric_readers=[reader]))
    r = _result(ex_dir)
    r["samples"][0]["trace_id"] = "ab" * 16
    t.record_run(r)
    spans = exp.get_finished_spans()
    names = {s.name for s in spans}
    assert {"genai_eval.run", "genai_eval.case", "genai_eval.grade"} <= names
    linked = [s for s in spans if s.name == "genai_eval.case" and s.links]
    assert linked and format(linked[0].links[0].context.trace_id, "032x") == "ab" * 16
    metrics = reader.get_metrics_data()
    assert any(m.name == "genai_eval.score" for rm in metrics.resource_metrics for sm in rm.scope_metrics for m in sm.metrics)


def test_cli_validate_run_and_check_config(ex_dir, tmp_path, capsys):
    assert cli(["validate", str(ex_dir / "policy_qa.yaml")]) == 0
    assert cli(["run", str(ex_dir / "claims_extraction.yaml"), "--target", "extractor-v2", "--out", str(tmp_path / "o"), "--no-cache"]) == 0
    assert cli(["run", str(ex_dir / "claims_extraction.yaml"), "--target", "extractor-v1", "--out", str(tmp_path / "o1"), "--no-cache"]) == 1
    assert cli(["check-config", str(ex_dir / "providers.live.yaml")]) == 0
    bad = tmp_path / "p.yaml"
    bad.write_text("profiles:\n  x: {provider: openai, model: m, options: {store: true}, pricing: {input_per_mtok: 1, output_per_mtok: 1}}\n")
    assert cli(["check-config", str(bad)]) == 1
