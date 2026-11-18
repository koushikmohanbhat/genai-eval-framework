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
from .sources import attach_history, build_source
from .sources.normalize import join_stats


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
        entry: dict[str, Any] = {"source": spec.name, "type": spec.type, "samples": len(got),
                                 "population": getattr(src, "population", len(got)), "sampling": spec.sampling,
                                 "window": spec.window, "sampling_method": "hash of session / trace id",
                                 "capture_rate": spec.capture_rate,
                                 # probability that a production session reaches this result (None when the
                                 # telemetry capture rate is unknown)
                                 "inclusion_probability": (spec.capture_rate * spec.sampling
                                                           if spec.capture_rate is not None else None),
                                 "fixture": bool(spec.fixture)}
        for j in spec.join:
            js = build_source({"name": j.get("name", f"{spec.name}:{j['type']}"), "window": spec.window, **j}, sources.base_dir,
                              clients.get(j.get("name", "")))
            if js.kind == "records":
                st = join_stats(got, js.join_records())
                entry[f"enriched:{j['type']}"] = st["filled"]
                entry[f"join:{j['type']}"] = st
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
        entry["content"] = {"of": len(got), "with_input": sum(1 for s in got if s.input),
                            "with_output": sum(1 for s in got if s.output)}
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
    runner.source_specs = list(sources.sources)
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
    apply_join_policy(result, sources, runner.tokenizer)
    return result


def apply_join_policy(result: dict[str, Any], sources: SourcesConfig, tokenizer: Any = None) -> dict[str, Any]:
    """Diagnose each source's joins; under `on_problem: invalid` make the affected targets' gates invalid and mark the
    run unreliable, so a scorecard built on mismatched payloads isn't read as a normal one."""
    from .sources.normalize import diagnose_source
    pol = sources.join_policy
    label = {s.name: (s.target or s.name) for s in sources.sources}
    problems: dict[str, list[str]] = {}
    for e in result.get("sources", []):
        if "samples" not in e:
            continue
        for k, st in e.items():         # sample pairs are for inspection: tokenise them like the rest of the result
            if k.startswith("join:") and isinstance(st, dict):
                st.pop("by_trace", None)
                st.pop("record_trace_ids", None)
            if k.startswith("join:") and isinstance(st, dict) and tokenizer:
                for p in st.get("pairs", []):
                    for f in ("span_output", "record_input", "record_output"):
                        if p.get(f):
                            p[f] = tokenizer.tokenize(p[f])
        pr = diagnose_source(e, pol.min_id_share, pol.max_time_share, pol.min_content_share)
        if pr:
            problems.setdefault(label.get(e["source"], e["source"]), []).extend(pr)
    status = "ok" if not problems else ("unreliable" if pol.on_problem == "invalid" else "questionable")
    result["telemetry_quality"] = {"status": status, "policy": pol.model_dump(), "problems": problems}
    if status == "unreliable":
        for g in result.get("gates", []):
            if g.get("target") in problems:
                g.update(status="invalid", passed=False,
                         message=("telemetry joins unreliable, so these scores may not belong to the traces they were read "
                                  "from: " + problems[g["target"]][0] + (f" (+{len(problems[g['target']]) - 1} more)"
                                                                         if len(problems[g["target"]]) > 1 else "")))
        result["passed"] = not any(g["severity"] == "block" and not g["passed"] for g in result.get("gates", []))
    if problems:
        result.setdefault("warnings", []).insert(
            0, f"telemetry joins {status} for {', '.join(sorted(problems))}: run `genai-evals sources check` "
               "(docs/sources.md, Troubleshooting telemetry joins)")
    return result


def run_online(*a, **kw) -> dict[str, Any]:
    return asyncio.run(run_online_async(*a, **kw))
