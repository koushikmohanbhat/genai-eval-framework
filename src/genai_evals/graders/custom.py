"""Extension points: Python functions, HTTP graders, AgentCore Evaluations evaluators, Vertex Gen AI evaluation metrics."""
from __future__ import annotations

import asyncio
import json
import urllib.request

from ..models import Case, Sample, Score
from ..targets.base import import_ref, maybe_await
from .base import Grader, register


@register("python")
class PythonGrader(Grader):
    """callable: module:function -> fn(sample, case, params) returning bool | float | dict(value, passed, reasoning, details)."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        fn = import_ref(self.p["callable"], self.ctx.suite.base_dir)
        res = await maybe_await(fn(sample, case, self.p))
        if isinstance(res, Score):
            return res
        if isinstance(res, bool):
            return self.score(sample, 1.0 if res else 0.0, passed=res)
        if isinstance(res, (int, float)):
            return self.score(sample, float(res))
        if isinstance(res, dict):
            if res.get("skip"):
                return self.skip(sample, str(res.get("reason", "skipped")))
            return self.score(sample, res.get("value"), passed=res.get("passed"), reasoning=res.get("reasoning"), details=res.get("details", {}))
        raise TypeError(f"python grader returned {type(res).__name__}")


@register("http")
class HttpGrader(Grader):
    """POST {sample, case} to `url`; expects {value, passed?, reasoning?, details?}. Sample text is tokenised first."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        tok = self.ctx.tokenize
        body = {"sample": json.loads(sample.model_copy(update={"output": tok(sample.output or "")}).model_dump_json()),
                "case": {"id": case.id, "input": tok(case.input_text()), "reference": case.reference, "tags": case.tags}}
        req = urllib.request.Request(self.p["url"], data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")

        def _do():
            with urllib.request.urlopen(req, timeout=float(self.p.get("timeout_s", 60))) as r:  # noqa: S310 - operator-configured URL
                return json.loads(r.read() or b"{}")
        res = await asyncio.to_thread(_do)
        return self.score(sample, res.get("value"), passed=res.get("passed"), reasoning=res.get("reasoning"), details=res.get("details", {}))


def sample_to_otel_spans(sample: Sample) -> list[dict]:
    """Minimal OTel GenAI-convention spans for a sample (used to call managed evaluators that read spans)."""
    trace_id = (sample.trace_id or f"{abs(hash((sample.case_id, sample.target, sample.repeat))):032x}")[:32]
    root = {"traceId": trace_id, "spanId": "0" * 15 + "1", "name": "invoke_agent", "kind": "SPAN_KIND_INTERNAL",
            "attributes": {"gen_ai.operation.name": "invoke_agent", "session.id": sample.session_id or sample.case_id,
                           "gen_ai.input.messages": json.dumps([{"role": "user", "content": str(sample.input)}]),
                           "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": sample.output}])}}
    spans = [root]
    for i, t in enumerate(sample.trajectory, 2):
        spans.append({"traceId": trace_id, "spanId": f"{i:016x}", "parentSpanId": root["spanId"], "name": f"execute_tool {t.name}",
                      "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": t.name,
                                     "gen_ai.tool.call.arguments": json.dumps(t.args), "gen_ai.tool.call.result": json.dumps(t.result, default=str)}})
    return spans


@register("agentcore_evaluator")
class AgentCoreEvaluator(Grader):
    """Experimental: call an Amazon Bedrock AgentCore Evaluations evaluator (built-in or custom) by id/ARN on this
    sample's spans. options: evaluator_id, region, operation (default 'evaluate'), score_field, max_score.
    The AgentCore Evaluations API surface is new; verify the request shape against your SDK version (docs/limitations.md)."""
    uses_model = True

    async def grade(self, sample: Sample, case: Case) -> Score:
        import boto3
        c = boto3.client("bedrock-agentcore", region_name=self.p.get("region"))
        op = getattr(c, self.p.get("operation", "evaluate"))
        req = {"evaluatorId": self.p["evaluator_id"], "evaluationInput": {"sessionSpans": sample_to_otel_spans(sample)}}
        req.update(self.p.get("request_extra", {}))
        res = await asyncio.to_thread(op, **req)
        results = res.get("evaluationResults") or [res]
        r0 = results[0] if results else {}
        raw = r0.get(self.p.get("score_field", "value"), r0.get("score"))
        mx = float(self.p.get("max_score", 1.0))
        value = (float(raw) / mx) if isinstance(raw, (int, float)) else None
        return self.score(sample, value, label=r0.get("label"), reasoning=r0.get("explanation"), details={"raw": r0})


@register("vertex_metric")
class VertexMetric(Grader):
    """Experimental: score with a Vertex AI Gen AI evaluation service metric (e.g. 'groundedness', 'safety', or a rubric
    metric). options: metric, project, location, max_score. Uses vertexai.evaluation.EvalTask on a one-row dataset."""
    uses_model = True

    async def grade(self, sample: Sample, case: Case) -> Score:
        import pandas as pd  # type: ignore
        import vertexai  # type: ignore
        from vertexai.evaluation import EvalTask  # type: ignore
        vertexai.init(project=self.p.get("project"), location=self.p.get("location"))
        tok = self.ctx.tokenize
        row = {"prompt": tok(case.input_text()), "response": tok(sample.output or "")}
        if sample.context:
            row["context"] = "\n".join(tok(c.text) for c in sample.context)
        if case.reference.get("answer"):
            row["reference"] = tok(str(case.reference["answer"]))
        metric = self.p["metric"]

        def _run():
            return EvalTask(dataset=pd.DataFrame([row]), metrics=[metric]).evaluate()
        res = await asyncio.to_thread(_run)
        table = res.metrics_table
        col = f"{metric}/score"
        raw = float(table[col].iloc[0]) if col in table else None
        mx = float(self.p.get("max_score", 5.0))
        expl = table[f"{metric}/explanation"].iloc[0] if f"{metric}/explanation" in table else None
        return self.score(sample, raw / mx if raw is not None else None, reasoning=expl)
