"""Promptfoo interop (OpenAI's named migration path off the hosted Evals platform).

export: suite -> promptfooconfig.yaml (prompts, providers, tests with mapped assertions)
import: promptfoo results JSON -> Samples + Scores, so Promptfoo runs appear in the same reports and gates.
Promptfoo is an interop target, not a dependency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..config import ProvidersConfig, SuiteSpec, load_cases
from ..models import Sample, Score, Usage
from ..prompts import PromptStore

PROVIDER_IDS = {
    "openai": lambda p: f"openai:responses:{p.model}",
    "openai_compatible": lambda p: f"openai:chat:{p.model}",
    "anthropic": lambda p: f"anthropic:messages:{p.model}",
    "vertex_anthropic": lambda p: f"vertex:{p.model}",
    "bedrock": lambda p: f"bedrock:{p.model}",
    "bedrock_anthropic": lambda p: f"bedrock:{p.model}",
    "vertex": lambda p: f"vertex:{p.model}",
    "mock": lambda p: "echo",
}


def _provider_entry(providers: ProvidersConfig, name: str) -> dict[str, Any]:
    p = providers.get(name)
    entry: dict[str, Any] = {"id": PROVIDER_IDS[p.provider](p), "label": name}
    cfg: dict[str, Any] = {}
    if p.region:
        cfg["region"] = p.region
    if p.project:
        cfg["projectId"] = p.project
    if p.location:
        cfg["region"] = p.location
    if p.base_url:
        cfg["apiBaseUrl"] = p.base_url
    if p.provider == "openai":
        cfg["store"] = p.options.get("store", False)
    if cfg:
        entry["config"] = cfg
    return entry


def _assertion(g, suite: SuiteSpec) -> tuple[dict[str, Any] | None, str | None]:
    p = g.params()
    t = g.type
    thr = {"threshold": g.threshold} if g.threshold is not None else {}
    if t == "exact":
        return {"type": "equals", "value": p.get("value", "{{reference_answer}}")}, None
    if t == "contains":
        v = p.get("value")
        if isinstance(v, list):
            return {"type": "not-icontains-any" if p.get("negate") else ("icontains-all" if p.get("mode", "all") == "all" else "icontains-any"), "value": v}, None
        return {"type": "not-icontains" if p.get("negate") else "icontains", "value": v or "{{reference_answer}}"}, None
    if t == "regex":
        return {"type": "not-regex" if p.get("negate") else "regex", "value": p["pattern"]}, None
    if t == "json_schema":
        schema = p.get("schema")
        if isinstance(schema, str):
            schema = json.loads(suite.path(schema).read_text(encoding="utf-8"))
        return {"type": "is-json", "value": schema}, None
    if t in ("rubric", "policy", "reference_match"):
        text = p.get("rubric") or p.get("policy") or "The output gives the same answer as: {{reference_answer}}"
        return {"type": "llm-rubric", "value": text, **thr}, None
    if t == "faithfulness":
        return {"type": "context-faithfulness", "threshold": g.threshold or 0.9}, None
    if t == "answer_relevance":
        return {"type": "answer-relevance", "threshold": g.threshold or 0.75}, None
    if t == "rouge":
        return {"type": "rouge-n", "value": "{{reference_answer}}", "threshold": g.threshold or 0.5}, None
    if t == "bleu":
        return {"type": "bleu", "value": "{{reference_answer}}", "threshold": g.threshold or 0.3}, None
    if t == "embedding_similarity":
        return {"type": "similar", "value": "{{reference_answer}}", "threshold": g.threshold or 0.8}, None
    if t == "latency":
        return {"type": "latency", "threshold": p["max_ms"]}, None
    if t == "cost":
        return {"type": "cost", "threshold": p["max_usd"]}, None
    if t == "python":
        mod, _, fn = p["callable"].partition(":")
        return {"type": "python", "value": f"file://{mod.replace('.', '/')}.py:{fn}"}, None
    return None, f"{g.name} ({t}) has no Promptfoo equivalent; it runs only in genai-evals"


def export_config(suite: SuiteSpec, providers: ProvidersConfig) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    store = PromptStore(suite.path(suite.prompts_dir)) if suite.prompts_dir else None
    prompts, provs = [], []
    for t in suite.targets:
        if t.kind not in ("model", "prompt", "rag") or not t.profile:
            notes.append(f"target '{t.name}' ({t.kind}/{t.framework or '-'}) is not exported: use a Promptfoo custom provider or http provider")
            continue
        if t.prompt and store:
            rp = store.render(t.prompt, t.prompt_stage, {})
            msgs = ([{"role": "system", "content": rp.system}] if rp.system else []) + [{"role": "user", "content": rp.template or "{{input}}"}]
            prompts.append({"id": f"{t.name}", "label": f"{rp.ref}", "raw": json.dumps(msgs)})
        else:
            msgs = ([{"role": "system", "content": t.system}] if t.system else []) + [{"role": "user", "content": t.template or "{{input}}"}]
            prompts.append({"id": t.name, "label": t.name, "raw": json.dumps(msgs)})
        e = _provider_entry(providers, t.profile)
        e["label"] = t.name
        e["prompts"] = [t.name]
        provs.append(e)
    asserts, default_test = [], {}
    for g in suite.graders:
        a, note = _assertion(g, suite)
        if a:
            a["metric"] = g.name
            asserts.append(a)
        if note:
            notes.append(note)
    if suite.judges:
        default_test["options"] = {"provider": _provider_entry(providers, suite.judges[0])["id"]}
        if len(suite.judges) > 1:
            notes.append(f"Promptfoo uses one grading provider; exported {suite.judges[0]} (the genai-evals panel also uses {suite.judges[1:]})")
    tests = []
    for c in load_cases(suite):
        vars_: dict[str, Any] = dict(c.input) if isinstance(c.input, dict) else {"input": c.input_text()}
        vars_.setdefault("input", c.input_text())
        if c.reference.get("answer") is not None:
            vars_["reference_answer"] = c.reference["answer"]
        if c.reference.get("context"):
            vars_["context"] = c.reference["context"]
        tests.append({"description": c.id, "vars": vars_, "metadata": {"tags": c.tags, "critical": c.critical}})
    if asserts:
        default_test["assert"] = asserts
    cfg = {"description": f"Exported from genai-evals suite '{suite.name}'", "prompts": prompts, "providers": provs,
           "defaultTest": default_test, "tests": tests}
    return cfg, notes


def write_config(suite: SuiteSpec, providers: ProvidersConfig, out: str | Path) -> list[str]:
    cfg, notes = export_config(suite, providers)
    header = "# Generated by `genai-evals promptfoo export`.\n" + "".join(f"# NOTE: {n}\n" for n in notes)
    Path(out).write_text(header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return notes


def import_results(path: str | Path) -> tuple[list[Sample], list[Score]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("results", data)
    if isinstance(rows, dict):
        rows = rows.get("results", [])
    samples, scores = [], []
    for i, r in enumerate(rows):
        tc = r.get("testCase") or {}
        case_id = tc.get("description") or (r.get("vars") or {}).get("id") or f"pf-{i}"
        prov = r.get("provider") or {}
        target = prov.get("label") or prov.get("id") or "promptfoo"
        resp = r.get("response") or {}
        tu = resp.get("tokenUsage") or {}
        out = resp.get("output")
        s = Sample(case_id=str(case_id), target=str(target), origin="import:promptfoo", input=r.get("vars") or tc.get("vars"),
                   output=out if isinstance(out, str) or out is None else json.dumps(out), latency_ms=r.get("latencyMs"), cost_usd=r.get("cost"),
                   usage=Usage(input_tokens=tu.get("prompt", 0) or 0, output_tokens=tu.get("completion", 0) or 0, cached_tokens=tu.get("cached", 0) or 0),
                   error=r.get("error"))
        samples.append(s)
        gr = r.get("gradingResult") or {}
        comps = gr.get("componentResults") or ([gr] if gr else [])
        for j, cr in enumerate(comps):
            a = cr.get("assertion") or {}
            name = a.get("metric") or a.get("type") or f"assert_{j}"
            scores.append(Score(grader=str(name), case_id=s.case_id, target=s.target, value=float(cr.get("score", 1.0 if cr.get("pass") else 0.0)),
                                passed=bool(cr.get("pass")), reasoning=cr.get("reason"), details={"promptfoo_type": a.get("type")}))
    return samples, scores
