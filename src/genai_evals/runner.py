"""Offline runner: plan (cases x targets x repeats) -> execute (async, cached) -> grade -> aggregate -> gate."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .calibration import load_calibration, untrusted_graders
from .config import ProvidersConfig, SuiteSpec, check_suite, load_cases
from .dpdp import _warn_once, build_tokenizer, tokenize_obj
from .gates import blocking_failures, evaluate_gates
from .graders import GradeContext, build_grader
from .graders.judges import JUDGE_GRADER_TYPES, judge_prompts_hash
from .models import Case, Sample, Score
from .prompts import PromptStore
from .providers import ProviderPool
from .stats import paired_delta, summarise
from .store import ResultStore
from .targets import RunContext, Target, build_target

CACHEABLE_KINDS = {"model", "prompt", "rag"}


class SuiteError(ValueError):
    pass


def git_sha() -> str | None:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False)
        return r.stdout.strip() if r.returncode == 0 and len(r.stdout.strip()) == 40 else None
    except (OSError, subprocess.SubprocessError):
        return None


class Runner:
    def __init__(self, suite: SuiteSpec, providers: ProvidersConfig, *, prompt_overrides: dict[str, str] | None = None,
                 cache_dir: str | Path | None = ".genai-evals/cache", use_cache: bool = True, clients: dict | None = None,
                 telemetry: Any = None, extra: dict[str, Any] | None = None):
        problems = check_suite(suite, providers)
        if problems:
            raise SuiteError("suite is not valid:\n  - " + "\n  - ".join(problems))
        self.suite = suite
        self.providers = providers
        self.pool = ProviderPool(providers, clients)
        prompts_dir = suite.path(suite.prompts_dir) if suite.prompts_dir else None
        self.prompts = PromptStore(prompts_dir) if prompts_dir else None
        self.ctx = RunContext(suite, providers, self.pool, self.prompts, dict(prompt_overrides or {}), dict(extra or {}))
        self.cache_dir = Path(cache_dir) if cache_dir and use_cache else None
        self.tokenizer = build_tokenizer(suite.tokenizer) if suite.tokenize else None
        self.telemetry = telemetry

    # ---- execution ---------------------------------------------------------
    def _cache_key(self, t: Target, case: Case, repeat: int) -> str | None:
        # The cache holds raw (untokenised) outputs, because graders such as pii_leak and field_match must see them.
        # With DPDP tokenisation on it is therefore off unless a target opts in explicitly.
        explicit = t.spec.options.get("cache")
        enabled = explicit if explicit is not None else (t.spec.kind in CACHEABLE_KINDS and not self.tokenizer)
        if not self.cache_dir or not enabled:
            return None
        if self.tokenizer and explicit:
            _warn_once(f"target '{t.name}' caches raw outputs in {self.cache_dir} (tokenisation is on); "
                       "restrict access to that directory and clear it after use")
        blob = json.dumps({"case": case.model_dump(), "target": t.config_fingerprint(), "repeat": repeat, "seed": self.suite.seed},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    async def _execute(self, t: Target, case: Case, repeat: int, sem: asyncio.Semaphore) -> Sample:
        key = self._cache_key(t, case, repeat)
        if key:
            p = self.cache_dir / f"{key}.json"
            if p.exists():
                s = Sample.model_validate_json(p.read_text(encoding="utf-8"))
                s.metadata["cached"] = True
                return s
        async with sem:
            try:
                s = await t.run(case, repeat)
            except Exception as e:  # noqa: BLE001 - a failing system is a result, not a crash
                s = Sample(case_id=case.id, target=t.name, repeat=repeat, input=case.input, error=f"{type(e).__name__}: {e}")
        if key and not s.error:
            self.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            p = self.cache_dir / f"{key}.json"
            p.write_text(s.model_dump_json(), encoding="utf-8")
            p.chmod(0o600)
        return s

    async def execute(self, targets: list[Target], cases: list[Case]) -> list[Sample]:
        sem = asyncio.Semaphore(max(1, self.suite.concurrency))
        jobs = [self._execute(t, c, r, sem) for t in targets for c in cases for r in range(max(1, self.suite.repeats))]
        return list(await asyncio.gather(*jobs))

    # ---- grading -------------------------------------------------------------
    def grade_context(self, samples: list[Sample], families: dict[str, str | None]) -> GradeContext:
        tok = self.tokenizer
        return GradeContext(suite=self.suite, pool=self.pool, providers=self.providers, target_families=families,
                            samples={(s.target, s.case_id, s.repeat): s for s in samples},
                            tokenize=(lambda x: tok.tokenize(x)) if tok else (lambda x: x),
                            calibration=load_calibration(self.suite.path(self.suite.calibration) if self.suite.calibration else None))

    async def grade(self, samples: list[Sample], cases: list[Case], gctx: GradeContext) -> list[Score]:
        by_id = {c.id: c for c in cases}
        graders = [build_grader(g, gctx) for g in self.suite.graders]
        sem = asyncio.Semaphore(max(1, self.suite.concurrency))

        async def one(gr, s: Sample) -> Score:
            case = by_id.get(s.case_id) or Case(id=s.case_id, input=s.input or "")
            if not gr.applies(case):
                return gr.skip(s, "not applicable to this case's tags")
            if s.error:
                return Score(grader=gr.name, case_id=s.case_id, target=s.target, repeat=s.repeat, value=0.0, passed=False,
                             error=f"target error: {s.error}")
            async with sem:
                try:
                    return await gr.grade(s, case)
                except Exception as e:  # noqa: BLE001
                    return Score(grader=gr.name, case_id=s.case_id, target=s.target, repeat=s.repeat, error=f"{type(e).__name__}: {e}")
        return list(await asyncio.gather(*[one(g, s) for s in samples for g in graders]))

    # ---- full run --------------------------------------------------------------
    async def run_async(self, target_names: list[str] | None = None, baseline: dict[str, Any] | None = None,
                        baseline_map: dict[str, str] | None = None) -> dict[str, Any]:
        cases = load_cases(self.suite)
        specs = [t for t in self.suite.targets if not target_names or t.name in target_names]
        if not specs:
            raise SuiteError(f"no targets selected (have: {[t.name for t in self.suite.targets]})")
        targets = [build_target(t, self.ctx) for t in specs]
        samples = await self.execute(targets, cases)
        families = {t.name: t.provider_family() for t in targets}
        gctx = self.grade_context(samples, families)
        scores = await self.grade(samples, cases, gctx)
        infos = [{"name": t.name, "kind": t.spec.kind, "profile": t.spec.profile, "family": t.provider_family(),
                  "framework": t.spec.framework, "prompt": t.spec.prompt} for t in targets]
        return self.finish(cases, infos, samples, scores, gctx, baseline, baseline_map)

    def run(self, target_names: list[str] | None = None, baseline: dict[str, Any] | None = None,
            baseline_map: dict[str, str] | None = None) -> dict[str, Any]:
        return asyncio.run(self.run_async(target_names, baseline, baseline_map))

    def finish(self, cases, targets: list[dict[str, Any]], samples, scores, gctx, baseline, baseline_map,
               extra: dict[str, Any] | None = None) -> dict[str, Any]:
        summary = {t["name"]: summarise(t["name"], cases, samples, scores, self.suite.seed, self.suite.on_error) for t in targets}
        base_summaries = baseline_summaries(baseline, list(summary), baseline_map)
        untrusted = untrusted_graders(gctx.calibration, self.suite.min_judge_kappa, self.suite.min_calibration_n)
        gate_results = evaluate_gates(self.suite.gates, summary, samples, base_summaries, self.suite.seed, untrusted,
                                      min_n=self.suite.min_n)
        comparisons = {}
        for tname, base in base_summaries.items():
            comparisons[tname] = {g: paired_delta(d["per_case"], base["graders"][g]["per_case"], self.suite.seed,
                                                  d.get("per_case_pass"), base["graders"][g].get("per_case_pass"))
                                  for g, d in summary[tname]["graders"].items() if g in base.get("graders", {})}
        if self.tokenizer:
            samples = [self._tokenize_sample(s) for s in samples]
            scores = [self._tokenize_score(sc) for sc in scores]
        prompt_versions: dict[str, dict[str, str]] = {}
        for s in samples:
            if s.prompt_versions:
                prompt_versions.setdefault(s.target, {}).update(s.prompt_versions)
        result = {
            "run_id": f"run_{uuid.uuid4().hex[:12]}", "suite": self.suite.name, "description": self.suite.description,
            "created_at": ResultStore.now(), "git_sha": git_sha(),
            "passed": not blocking_failures(gate_results),
            "targets": targets,
            "judges": self.suite.judges, "repeats": self.suite.repeats,
            "cases": [{"id": c.id, "tags": c.tags, "critical": c.critical, "input": (self.tokenizer.tokenize(c.input_text()) if self.tokenizer else c.input_text())[:500]}
                      for c in cases],
            "graders": [_grader_info(g) for g in self.suite.graders],
            "judge_provenance": self._judge_provenance(scores),
            "summary": summary, "gates": [asdict(g) for g in gate_results], "comparisons": comparisons,
            "baseline": (baseline or {}).get("run_id"), "prompt_versions": prompt_versions,
            "calibration": {g: {k: d.get(k) for k in ("kappa", "kappa_ci", "accuracy", "n", "human_pass_rate", "warnings")}
                            for g, d in gctx.calibration.items()},
            "data_handling": {"tokenizer": self.tokenizer.describe() if self.tokenizer and hasattr(self.tokenizer, "describe")
                              else ({"type": "custom"} if self.tokenizer else None), "on_error": self.suite.on_error,
                              "min_n": self.suite.min_n},
            "untrusted_graders": untrusted,
            "samples": [json.loads(s.model_dump_json()) for s in samples], "scores": [json.loads(s.model_dump_json()) for s in scores],
        }
        result.update(extra or {})
        result.setdefault("mode", "offline")
        if self.suite.report:
            result["report"] = self.suite.report
        if self.suite.pack:
            result["pack"] = self.suite.pack
        result["adapters"] = self.adapter_status()
        from .results import stamp
        stamp(result)
        if self.telemetry:
            self.telemetry.record_run(result)
        return result

    source_specs: list | None = None          # set by online runs, so reports list the telemetry adapters used

    def validation_path(self) -> Path | None:
        if self.suite.validation:
            return self.suite.path(self.suite.validation)
        p = Path(self.suite.base_dir) / "validation.yaml"
        return p if p.is_file() else None

    def adapter_status(self) -> list[dict[str, Any]]:
        """Every adapter this run touched, with its effective maturity (built-in + the project's validation.yaml)."""
        from .plugins import adapters_used, effective, load_validation
        recs = load_validation(self.validation_path())
        return [effective(k, n, recs) for k, n in adapters_used(self.suite, self.providers, self.source_specs)]

    def _judge_provenance(self, scores: list[Score]) -> dict[str, Any]:
        """What the judges were, exactly: configured profile, settings, and the model versions providers reported."""
        if not self.suite.judges:
            return {}
        seen: dict[str, set[str]] = {}
        for sc in scores:
            for j in (sc.details or {}).get("judges", []) or []:
                if isinstance(j, dict) and j.get("judge") and j.get("model"):
                    seen.setdefault(j["judge"], set()).add(str(j["model"]))
        out: dict[str, Any] = {"prompt_templates": judge_prompts_hash(), "temperature": 0.0, "response_format": "json",
                               "policy": self.suite.judge_policy, "judges": {}}
        for name in self.suite.judges:
            p = self.providers.get(name)
            out["judges"][name] = {
                "provider": getattr(p, "provider", None), "configured_model": getattr(p, "model", None),
                "family": getattr(p, "model_family", None), "region": getattr(p, "region", None) or getattr(p, "location", None),
                "options": {k: v for k, v in (getattr(p, "options", None) or {}).items() if not k.startswith("_")},
                "reported_models": sorted(seen.get(name, set())),
            }
        return out

    def _tokenize_score(self, sc: Score) -> Score:
        """Deterministic graders run on raw values and may quote them in reasoning / details (e.g. field_match)."""
        tok = self.tokenizer
        d = json.loads(sc.model_dump_json())
        for k in ("reasoning", "details", "error", "label", "skip_reason"):
            d[k] = tokenize_obj(d.get(k), tok)
        return Score(**d)

    def _tokenize_sample(self, s: Sample) -> Sample:
        tok = self.tokenizer
        d = json.loads(s.model_dump_json())
        for k in ("input", "output", "output_json", "messages", "final_state", "error", "metadata"):
            d[k] = tokenize_obj(d.get(k), tok)
        for c in d.get("context", []):
            c["text"] = tok.tokenize(c.get("text", ""))
        for t in d.get("trajectory", []):
            t["args"] = tokenize_obj(t.get("args"), tok)
            t["result"] = tokenize_obj(t.get("result"), tok)
        return Sample(**d)


def _grader_info(g) -> dict[str, Any]:
    spec = g.model_dump(mode="json")
    info = {"name": g.name, "type": g.type,
            "config_hash": "sha256:" + hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]}
    if g.type in JUDGE_GRADER_TYPES:
        info["judge_prompt_hash"] = judge_prompts_hash()
    return info


def baseline_summaries(baseline: dict[str, Any] | None, targets: list[str], mapping: dict[str, str] | None = None) -> dict[str, dict]:
    if not baseline:
        return {}
    bs = baseline.get("summary", baseline)
    out = {}
    for t in targets:
        src = (mapping or {}).get(t, t)
        if src in bs:
            out[t] = bs[src]
    return out


def load_result(path: str | Path) -> dict[str, Any]:
    """Read a result (any 1.x schema, or a pre-versioning result, upgraded in memory)."""
    from .results import upgrade
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return upgrade(data) if "summary" in data and "samples" in data else data     # trimmed baselines pass through


def baseline_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Trimmed copy kept under version control or in the store: summaries (with per-case values) but no raw samples."""
    return {"run_id": result["run_id"], "suite": result["suite"], "created_at": result["created_at"], "git_sha": result.get("git_sha"),
            "prompt_versions": result.get("prompt_versions", {}), "summary": result["summary"]}


def compare_results(cur: dict[str, Any], base: dict[str, Any], seed: int = 7, mapping: dict[str, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    bs = baseline_summaries(base, list(cur["summary"]), mapping)
    for t, b in bs.items():
        out[t] = {g: paired_delta(d["per_case"], b["graders"][g]["per_case"], seed, d.get("per_case_pass"), b["graders"][g].get("per_case_pass"))
                  for g, d in cur["summary"][t]["graders"].items() if g in b["graders"]}
    return out
