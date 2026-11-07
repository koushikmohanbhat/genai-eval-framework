"""Report writers (the `report` adapter contract). Each renders a result to one file; plugins add more formats.

Flat exports (csv, jsonl) and the Promptfoo results shape let results be read without this framework, a specific
database or a specific CI system.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..plugins import ReportWriter


class HtmlReport(ReportWriter):
    name, filename = "html", "report.html"

    def render(self, result):
        from .html import render_report
        return render_report(result)


class ScorecardReport(ReportWriter):
    name, filename = "scorecard", "scorecard.html"

    def render(self, result):
        from .html import render_scorecard
        return render_scorecard(result)


class MarkdownReport(ReportWriter):
    name, filename = "markdown", "summary.md"

    def render(self, result):
        from .text import markdown_summary
        return markdown_summary(result)


class JUnitReport(ReportWriter):
    name, filename = "junit", "junit.xml"

    def render(self, result):
        from .text import junit_xml
        return junit_xml(result)


class JsonReport(ReportWriter):
    name, filename = "json", "result.json"

    def render(self, result):
        return json.dumps(result, indent=1, default=str)


SCORE_COLUMNS = ["run_id", "suite", "target", "case_id", "repeat", "grader", "value", "passed", "skipped", "error", "label",
                 "tags", "critical", "prompt_versions", "latency_ms", "cost_usd"]


def score_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per score, joined with case tags and the sample's ops numbers: the long format most tools read."""
    cases = {c["id"]: c for c in result.get("cases", [])}
    samples = {(s["target"], s["case_id"], s.get("repeat", 0)): s for s in result.get("samples", [])}
    rows = []
    for sc in result.get("scores", []):
        c = cases.get(sc["case_id"], {})
        s = samples.get((sc["target"], sc["case_id"], sc.get("repeat", 0)), {})
        rows.append({"run_id": result.get("run_id"), "suite": result.get("suite"), "target": sc["target"], "case_id": sc["case_id"],
                     "repeat": sc.get("repeat", 0), "grader": sc["grader"], "value": sc.get("value"), "passed": sc.get("passed"),
                     "skipped": sc.get("skipped", False), "error": sc.get("error"), "label": sc.get("label"),
                     "tags": "|".join(c.get("tags", [])), "critical": c.get("critical", False),
                     "prompt_versions": json.dumps(s.get("prompt_versions") or {}), "latency_ms": s.get("latency_ms"), "cost_usd": s.get("cost_usd")})
    return rows


class CsvScores(ReportWriter):
    name, filename = "csv", "scores.csv"

    def render(self, result):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=SCORE_COLUMNS)
        w.writeheader()
        w.writerows(score_rows(result))
        return buf.getvalue()


class JsonlScores(ReportWriter):
    name, filename = "jsonl", "scores.jsonl"

    def render(self, result):
        return "".join(json.dumps(r, default=str) + "\n" for r in score_rows(result))


class PromptfooResults(ReportWriter):
    """Results in Promptfoo's results-file shape (one entry per sample, graders as component results), so they can
    be viewed next to Promptfoo runs. Round-trips with `genai-evals promptfoo import`."""
    name, filename = "promptfoo", "promptfoo-results.json"

    def render(self, result):
        by = {}
        for sc in result.get("scores", []):
            by.setdefault((sc["target"], sc["case_id"], sc.get("repeat", 0)), []).append(sc)
        rows = []
        for s in result.get("samples", []):
            comps = [{"pass": bool(sc.get("passed")), "score": sc.get("value") if sc.get("value") is not None else 0.0, "reason": sc.get("reasoning") or "",
                      "assertion": {"type": "genai-evals", "metric": sc["grader"]}}
                     for sc in by.get((s["target"], s["case_id"], s.get("repeat", 0)), []) if not sc.get("skipped")]
            u = s.get("usage") or {}
            rows.append({"testCase": {"description": s["case_id"]}, "vars": {"id": s["case_id"]}, "provider": {"id": s["target"], "label": s["target"]},
                         "response": {"output": s.get("output"), "tokenUsage": {"prompt": u.get("input_tokens", 0), "completion": u.get("output_tokens", 0),
                                                                                "cached": u.get("cached_tokens", 0)}},
                         "latencyMs": s.get("latency_ms"), "cost": s.get("cost_usd"), "error": s.get("error"),
                         "success": all(c["pass"] for c in comps) if comps else None,
                         "score": (sum(c["score"] for c in comps) / len(comps)) if comps else None,
                         "gradingResult": {"pass": all(c["pass"] for c in comps), "componentResults": comps} if comps else None})
        return json.dumps({"version": 3, "results": {"results": rows}, "metadata": {"exportedBy": "genai-evals", "run_id": result.get("run_id")}},
                          indent=1, default=str)


def writer(name: str) -> ReportWriter:
    from ..plugins import load
    return load("report", name)()
