"""OpenTelemetry emission: eval runs become spans and metrics, linked to the original agent traces.

Spans: genai_eval.run > genai_eval.case > genai_eval.grade. A case span carries a link to the production trace it
graded (online evals), so a failing trace in X-Ray / Cloud Trace / Langfuse can be opened next to its scores.
Metrics: genai_eval.score (histogram), genai_eval.pass (counter), genai_eval.judge_cost (counter).
Export with any OTel exporter; for AWS / Google Cloud use the collector configs in collector/ (ADOT, Google-built
OpenTelemetry Collector), which also tokenise prompts before they leave the process.
"""
from __future__ import annotations

from typing import Any


def configure(exporter: str = "otlp", endpoint: str | None = None, service_name: str = "genai-evals", headers: dict[str, str] | None = None):
    """Set global tracer/meter providers. exporter: otlp | console | none."""
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    res = Resource.create({"service.name": service_name})
    tp = TracerProvider(resource=res)
    readers = []
    if exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        base = (endpoint or "http://localhost:4318").rstrip("/")
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces", headers=headers)))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics", headers=headers)))
    elif exporter == "console":
        tp.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(MeterProvider(resource=res, metric_readers=readers))
    return tp


def _link(trace_id: str | None):
    from opentelemetry import trace
    if not trace_id:
        return None
    h = trace_id.replace("-", "")
    if h.startswith("1") and len(h) == 33:      # X-Ray id 1-5f84c7a1-... -> W3C
        h = h[1:]
    if len(h) != 32:
        return None
    try:
        ctx = trace.SpanContext(trace_id=int(h, 16), span_id=1, is_remote=True, trace_flags=trace.TraceFlags(1))
    except ValueError:
        return None
    return trace.Link(ctx)


class Telemetry:
    def __init__(self, tracer_provider=None, meter_provider=None):
        from opentelemetry import metrics, trace
        self.tracer = (tracer_provider or trace.get_tracer_provider()).get_tracer("genai_evals")
        meter = (meter_provider or metrics.get_meter_provider()).get_meter("genai_evals")
        self.h_score = meter.create_histogram("genai_eval.score", description="eval score 0..1")
        self.c_pass = meter.create_counter("genai_eval.pass", description="graded samples by outcome")
        self.c_cost = meter.create_counter("genai_eval.judge_cost", unit="USD")

    def record_run(self, result: dict[str, Any]) -> None:
        by_sample: dict[tuple, list[dict]] = {}
        for s in result["scores"]:
            by_sample.setdefault((s["target"], s["case_id"], s["repeat"]), []).append(s)
        with self.tracer.start_as_current_span("genai_eval.run", attributes={
                "genai_eval.suite": result["suite"], "genai_eval.run_id": result["run_id"], "genai_eval.passed": result["passed"]}):
            for smp in result["samples"]:
                pv = smp.get("prompt_versions") or {}
                attrs = {"genai_eval.case_id": smp["case_id"], "genai_eval.target": smp["target"], "genai_eval.repeat": smp["repeat"],
                         "genai_eval.origin": smp.get("origin", "offline")}
                for k, v in pv.items():
                    attrs[f"prompt.version.{k}"] = v
                if smp.get("trace_id"):
                    attrs["genai_eval.source_trace_id"] = smp["trace_id"]
                if smp.get("session_id"):
                    attrs["session.id"] = smp["session_id"]
                link = _link(smp.get("trace_id"))
                with self.tracer.start_as_current_span("genai_eval.case", attributes=attrs, links=[link] if link else None):
                    for sc in by_sample.get((smp["target"], smp["case_id"], smp["repeat"]), []):
                        ga = {"genai_eval.grader": sc["grader"], "genai_eval.target": sc["target"], "genai_eval.suite": result["suite"]}
                        if pv:
                            ga["prompt.version"] = ",".join(f"{k}@{v}" for k, v in pv.items())
                        with self.tracer.start_as_current_span("genai_eval.grade", attributes={
                                **ga, "genai_eval.value": sc["value"] if sc.get("value") is not None else -1.0,
                                "genai_eval.passed": bool(sc.get("passed")), "genai_eval.skipped": bool(sc.get("skipped"))}):
                            pass
                        if sc.get("value") is not None:
                            self.h_score.record(sc["value"], ga)
                        if not sc.get("skipped"):
                            self.c_pass.add(1, {**ga, "outcome": "pass" if sc.get("passed") else "fail"})
                        if sc.get("cost_usd"):
                            self.c_cost.add(sc["cost_usd"], ga)
