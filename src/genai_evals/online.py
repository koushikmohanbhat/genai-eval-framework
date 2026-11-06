"""Online evals: pull sampled production traffic from native sources, grade it with the same graders, gate and publish.

    genai-evals online --sources sources.yaml --suite online_checks.yaml --publish cloudwatch,cloud_monitoring

Runs inside the customer's own AWS account / Google Cloud project (Lambda, ECS task or Cloud Run job).
Only reference-free graders make sense here (faithfulness, policy, pii_leak, tool checks, latency, cost, rubric
without a reference); cases are synthesised from production samples and tagged with their prompt versions.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .config import ProvidersConfig, SourcesConfig, SuiteSpec
from .models import Case, Sample, Score
from .runner import Runner
from .sources import attach_history, build_source, enrich


def collect(sources: SourcesConfig, clients: dict[str, Any] | None = None) -> tuple[list[Sample], list[Score], dict[str, Any], list[dict]]:
    clients = clients or {}
    samples: list[Sample] = []
    imported: list[Score] = []
    operational: dict[str, Any] = {}
    log: list[dict] = []
    for spec in sources.sources:
        src = build_source(spec, sources.base_dir, clients.get(spec.name))
        if src.kind == "metrics":
            operational[spec.name] = src.metrics()
            log.append({"source": spec.name, "type": spec.type, "metrics": len(operational[spec.name])})
            continue
        if src.kind == "scores":
            sc = src.scores()
            imported += sc
            log.append({"source": spec.name, "type": spec.type, "scores": len(sc)})
            continue
        got = src.samples()
        label = spec.target or spec.name
        for s in got:
            s.target = label
        entry: dict[str, Any] = {"source": spec.name, "type": spec.type, "samples": len(got)}
        for j in spec.join:
            js = build_source({"name": j.get("name", f"{spec.name}:{j['type']}"), "window": spec.window, **j}, sources.base_dir,
                              clients.get(j.get("name", "")))
            if js.kind == "records":
                entry[f"enriched:{j['type']}"] = enrich(got, js.join_records())
            elif js.kind == "history":
                entry[f"history:{j['type']}"] = attach_history(got, js.history([s.session_id for s in got if s.session_id]))
            elif js.kind == "scores":
                sc = js.scores()
                ids = {s.session_id: s.case_id for s in got if s.session_id} | {s.trace_id: s.case_id for s in got if s.trace_id}
                for x in sc:
                    x.case_id = ids.get(x.case_id, x.case_id)
                    x.target = label
                imported += sc
                entry[f"scores:{j['type']}"] = len(sc)
            elif js.kind == "metrics":
                operational[f"{spec.name}:{j['type']}"] = js.metrics()
        samples += got
        log.append(entry)
    return samples, imported, operational, log


def cases_from_samples(samples: list[Sample]) -> list[Case]:
    seen: dict[str, Case] = {}
    for s in samples:
        tags = [f"prompt:{k}@{v}" for k, v in s.prompt_versions.items()] + [f"source:{s.target}"]
        if s.model:
            tags.append(f"model:{s.model}")
        seen.setdefault(s.case_id, Case(id=s.case_id, input=s.input if isinstance(s.input, (str, list, dict)) else str(s.input or ""),
                                        tags=tags, source="production", metadata={"principal": s.metadata.get("principal")}))
    return list(seen.values())


async def run_online_async(suite: SuiteSpec, providers: ProvidersConfig, sources: SourcesConfig, clients: dict[str, Any] | None = None,
                           baseline: dict[str, Any] | None = None, telemetry: Any = None) -> dict[str, Any]:
    samples, imported, operational, log = collect(sources, clients)
    runner = Runner(suite, providers, use_cache=False, telemetry=telemetry)
    # de-duplicate case ids across sources
    seen: set[tuple[str, str]] = set()
    uniq = []
    for s in samples:
        k = (s.target, s.case_id)
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    samples = uniq
    cases = cases_from_samples(samples)
    gctx = runner.grade_context(samples, {})
    scores = await runner.grade(samples, cases, gctx)
    scores += imported
    targets = sorted({s.target for s in samples})
    infos = [{"name": t, "kind": "source", "profile": None, "family": None, "framework": None, "prompt": None} for t in targets]
    graders = list(suite.graders)
    from .config import GraderSpec
    for name in sorted({s.grader for s in imported} - {g.name for g in graders}):
        graders.append(GraderSpec(name=name, type="imported"))
    runner.suite = suite.model_copy(update={"graders": graders})
    result = runner.finish(cases, infos, samples, scores, gctx, baseline, None,
                           extra={"mode": "online", "operational": operational, "sources": log})
    result["graders"] = [{"name": g.name, "type": g.type} for g in graders]
    return result


def run_online(*a, **kw) -> dict[str, Any]:
    return asyncio.run(run_online_async(*a, **kw))
