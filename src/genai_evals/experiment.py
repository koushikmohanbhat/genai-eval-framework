"""Experiments: a baseline and candidate changes, run on the same cases, compared side by side, and saved so they
can be reproduced.

An experiment file names a suite, the suite target to vary, a baseline and a list of variants. Each variant is a
small change to that target (prompt version, provider profile, model options, retriever, or any other target field).
All variants run in one run, on the same cases, with the same seed, repeats and judges, so differences come from
the change and not from the setup.

Output folder:

    manifest.json       what is needed to reproduce: file and dataset hashes, prompt hashes, providers used (no
                        secrets), env var names, framework / Python version, git commit, seed, repeats
    experiment.yaml     copy of the experiment file
    comparison.json     per-variant comparison with the baseline (see comparison.py)
    comparison.md/.html readable versions
    run/                the full run: result.json, report.html, scorecard.html, summary.md, junit.xml
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .comparison import compare_pair, n_tests
from .config import _ENV_REF, ProvidersConfig, SuiteSpec, TargetSpec, _load_yaml, load_cases, load_providers, load_suite
from .prompts import PromptStore
from .runner import Runner, SuiteError, git_sha

_DELTA_RULE = re.compile(r"\b(delta|delta_ci_low|delta_ci_high|significant)\s*\(")
_SECRETISH = re.compile(r"key|secret|token|password|credential", re.I)


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    prompt: str | None = None                  # e.g. claims_agent@1.4.0
    profile: str | None = None                 # provider profile, e.g. another model or cloud
    options: dict[str, Any] = Field(default_factory=dict)        # merged into the target's options
    retriever: dict[str, Any] | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)      # any other target field, e.g. {max_steps: 8}


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    hypothesis: str = ""
    suite: str
    providers: str | None = None
    target: str | None = None                  # suite target to vary (required if the suite has several)
    baseline: VariantSpec = Field(default_factory=lambda: VariantSpec(name="baseline"))
    variants: list[VariantSpec]
    graders: list[str] | None = None           # compare only these graders (default: all)
    repeats: int | None = None                 # override the suite's
    seed: int | None = None
    alpha: float = 0.05
    correction: Literal["bonferroni", "none"] = "bonferroni"
    base_dir: str = "."

    @model_validator(mode="after")
    def _check(self):
        if not self.variants:
            raise ValueError("an experiment needs at least one variant")
        names = [self.baseline.name] + [v.name for v in self.variants]
        if len(names) != len(set(names)):
            raise ValueError(f"baseline and variant names must be unique (got {names})")
        if not 0 < self.alpha < 1:
            raise ValueError("alpha must be between 0 and 1")
        return self

    def path(self, p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else Path(self.base_dir) / pp


def load_experiment(path: str | Path) -> ExperimentSpec:
    data = _load_yaml(path)
    data.setdefault("base_dir", str(Path(path).parent))
    return ExperimentSpec(**data)


def providers_path(exp: ExperimentSpec, override: str | None = None) -> str | None:
    if override:
        return override
    if exp.providers:
        return str(exp.path(exp.providers))
    cand = exp.path(exp.suite).parent / "providers.yaml"
    return str(cand) if cand.exists() else None


def _variant_target(base: TargetSpec, v: VariantSpec) -> TargetSpec:
    d = base.model_dump()
    d.update(v.overrides)
    for k in ("prompt", "profile", "retriever"):
        if getattr(v, k) is not None:
            d[k] = getattr(v, k)
    d["options"] = {**(d.get("options") or {}), **v.options}
    d["name"] = v.name
    return TargetSpec(**d)


def _changes(base: TargetSpec, t: TargetSpec) -> dict[str, Any]:
    a, b = base.model_dump(), t.model_dump()
    out = {k: b.get(k) for k in sorted(set(a) | set(b)) if k != "name" and a.get(k) != b.get(k)}
    if "options" in out:
        out["options"] = {k: v for k, v in (b.get("options") or {}).items() if (a.get("options") or {}).get(k) != v}
    return out


def build_suite(exp: ExperimentSpec, providers: ProvidersConfig) -> tuple[SuiteSpec, TargetSpec, list[str]]:
    """The suite with its target replaced by baseline + variants. Gates that compare with a baseline run
    (delta / significant) are dropped: the experiment comparison replaces them."""
    suite = load_suite(exp.path(exp.suite))
    if exp.target:
        base = next((t for t in suite.targets if t.name == exp.target), None)
        if base is None:
            raise SuiteError(f"experiment target '{exp.target}' is not in suite '{suite.name}' (have: {[t.name for t in suite.targets]})")
    elif len(suite.targets) == 1:
        base = suite.targets[0]
    else:
        raise SuiteError(f"suite '{suite.name}' has {len(suite.targets)} targets: set `target:` in the experiment")
    suite.targets = [_variant_target(base, exp.baseline)] + [_variant_target(base, v) for v in exp.variants]
    dropped = [g.name for g in suite.gates if _DELTA_RULE.search(g.rule)]
    suite.gates = [g for g in suite.gates if not _DELTA_RULE.search(g.rule)]
    for g in suite.gates:
        g.targets = None
    if exp.repeats:
        suite.repeats = exp.repeats
    if exp.seed is not None:
        suite.seed = exp.seed
    if exp.graders:
        unknown = set(exp.graders) - {g.name for g in suite.graders}
        if unknown:
            raise SuiteError(f"experiment compares unknown grader(s): {sorted(unknown)}")
    return suite, base, dropped


# ---- reproducibility --------------------------------------------------------
def _sha(path: Path) -> str | None:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _git_dirty(cwd: Path) -> bool | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:
        return None


def tracked_files(exp_path: Path, exp: ExperimentSpec, suite: SuiteSpec, prov_path: str | None) -> dict[str, Path]:
    files = {"experiment": exp_path, "suite": exp.path(exp.suite)}
    if prov_path:
        files["providers"] = Path(prov_path)
    if isinstance(suite.dataset, str):
        files["dataset"] = suite.path(suite.dataset)
    if suite.calibration:
        files["calibration"] = suite.path(suite.calibration)
    # local Python behind targets and tools (module files next to the suite); other code is pinned by the git commit
    for t in suite.targets:
        for ref in (t.callable, t.tools_module):
            if ref:
                mod = ref.split(":")[0]
                f = suite.path(mod.replace(".", "/") + ".py")
                if f.is_file():
                    files[f"code:{mod}"] = f
    for g in suite.graders:
        ref = g.params().get("callable") if hasattr(g, "params") else None
        if isinstance(ref, str):
            f = suite.path(ref.split(":")[0].replace(".", "/") + ".py")
            if f.is_file():
                files[f"code:{ref.split(':')[0]}"] = f
    return files


def _prompt_hashes(suite: SuiteSpec, result: dict[str, Any]) -> dict[str, str]:
    if not suite.prompts_dir:
        return {}
    store = PromptStore(suite.path(suite.prompts_dir))
    out = {}
    for pv in result.get("prompt_versions", {}).values():
        for pid, ver in pv.items():
            try:
                out[f"{pid}@{ver}"] = store.get(pid, ver).hash
            except Exception:
                out[f"{pid}@{ver}"] = "unresolved"
    return dict(sorted(out.items()))


def _profiles_used(suite: SuiteSpec, providers: ProvidersConfig) -> dict[str, Any]:
    names = {t.profile for t in suite.targets if t.profile} | set(suite.judges) | {g for gr in suite.graders for g in (gr.judges or [])}
    names |= {n for n in (suite.simulator, suite.embeddings) if n}
    out = {}
    for n in sorted(names):
        if n not in providers.profiles:
            continue
        p = providers.get(n)
        out[n] = {"provider": p.provider, "model": p.model, "family": p.model_family, "region": p.region,
                  "location": p.location, "options": {k: v for k, v in p.options.items() if not _SECRETISH.search(k)}}
    return out


def _env_names(files: dict[str, Path]) -> list[str]:
    names: set[str] = set()
    for p in files.values():
        if p.is_file() and p.suffix in {".yaml", ".yml"}:
            text = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in p.read_text(encoding="utf-8").splitlines())
            names |= {m.group(1) for m in _ENV_REF.finditer(text)}
    return sorted(names)


def build_manifest(exp: ExperimentSpec, exp_path: Path, suite: SuiteSpec, providers: ProvidersConfig,
                   prov_path: str | None, base: TargetSpec, result: dict[str, Any], dropped: list[str]) -> dict[str, Any]:
    files = tracked_files(exp_path, exp, suite, prov_path)
    return {
        "experiment": exp.name, "created_at": result["created_at"], "run_id": result["run_id"],
        "experiment_path": str(exp_path.resolve()),
        "files": {k: {"path": str(p.resolve()), "sha256": _sha(p)} for k, p in files.items()},
        "prompts": _prompt_hashes(suite, result),
        "target": {"base": base.name, "baseline": exp.baseline.name, "variants": [v.name for v in exp.variants],
                   "specs": {t.name: t.model_dump() for t in suite.targets}},
        "providers_used": _profiles_used(suite, providers),
        "judges": {"names": suite.judges, "provenance": result.get("judge_provenance"), "graders": result.get("graders")},
        "env_vars": _env_names(files),
        "run": {"seed": suite.seed, "repeats": suite.repeats, "n_cases": len(result.get("cases", [])),
                "tokenize": suite.tokenize, "on_error": suite.on_error, "min_n": suite.min_n, "dropped_gates": dropped},
        "environment": {"framework": __version__, "python": platform.python_version(), "platform": platform.platform(),
                        "git_sha": git_sha(), "git_dirty": _git_dirty(exp_path.parent)},
        "not_captured": ["Python code outside the suite folder, and installed packages (pinned only by the git commit and "
                         "framework version)",
                         "model behaviour behind a provider model id (providers can change it)",
                         "values of environment variables (names only)"],
    }


def verify_manifest(manifest: dict[str, Any], suite: SuiteSpec | None = None) -> list[str]:
    """Differences between the files / prompts recorded in a manifest and what is on disk now."""
    problems = []
    for k, f in manifest.get("files", {}).items():
        p = Path(f["path"])
        now = _sha(p)
        if now != f["sha256"]:
            problems.append(f"{k} changed: {p}" if now else f"{k} missing: {p}")
    if suite is not None and suite.prompts_dir:
        store = PromptStore(suite.path(suite.prompts_dir))
        for ref, h in manifest.get("prompts", {}).items():
            pid, _, ver = ref.partition("@")
            try:
                now = store.get(pid, ver).hash
            except Exception:
                now = None
            if now != h:
                problems.append(f"prompt {ref} changed" if now else f"prompt {ref} missing")
    return problems


# ---- run --------------------------------------------------------------------
def compare_experiment(exp: ExperimentSpec, suite: SuiteSpec, base: TargetSpec, result: dict[str, Any],
                       dropped: list[str]) -> dict[str, Any]:
    summary = result["summary"]
    graders = exp.graders or [g.name for g in suite.graders]
    m = n_tests(len(exp.variants), len(graders)) if exp.correction == "bonferroni" else 1
    a = exp.alpha / m
    results = {v.name: compare_pair(summary[exp.baseline.name], summary[v.name], cases=result["cases"],
                                    base_samples=result["samples"], cand_samples=result["samples"], seed=suite.seed,
                                    alpha=a, min_n=suite.min_n, graders=graders)
               for v in exp.variants}
    specs = {t.name: t for t in suite.targets}
    gates: dict[str, list[dict[str, Any]]] = {}
    for g in result["gates"]:
        gates.setdefault(g["target"], []).append({"gate": g["gate"], "status": g.get("status"), "severity": g["severity"],
                                                  "passed": g["passed"], "message": g.get("message")})
    return {"name": exp.name, "description": exp.description, "hypothesis": exp.hypothesis,
            "baseline": exp.baseline.name, "baseline_description": exp.baseline.description,
            "baseline_changes": _changes(base, specs[exp.baseline.name]),
            "variants": [{"name": v.name, "description": v.description, "changes": _changes(specs[exp.baseline.name], specs[v.name])}
                         for v in exp.variants],
            "alpha": exp.alpha, "correction": exp.correction, "tests": m, "alpha_per_test": a,
            "n_cases": len(result["cases"]), "repeats": result.get("repeats"), "run_id": result["run_id"],
            "results": results, "gates": gates, "dropped_gates": dropped}


def run_experiment(exp_path: str | Path, *, providers_override: str | None = None, use_cache: bool = True,
                   telemetry=None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run an experiment. Returns (result, comparison, manifest)."""
    exp_path = Path(exp_path)
    exp = load_experiment(exp_path)
    prov_path = providers_path(exp, providers_override)
    providers = load_providers(prov_path)
    suite, base, dropped = build_suite(exp, providers)
    load_cases(suite)
    result = Runner(suite, providers, use_cache=use_cache, telemetry=telemetry).run()
    result["experiment"] = exp.name
    comparison = compare_experiment(exp, suite, base, result, dropped)
    manifest = build_manifest(exp, exp_path, suite, providers, prov_path, base, result, dropped)
    return result, comparison, manifest


def write_experiment(out: str | Path, exp_path: str | Path, result: dict[str, Any], comparison: dict[str, Any],
                     manifest: dict[str, Any]) -> dict[str, str]:
    from .report import write_outputs
    from .report.experiment import render_comparison_html, render_comparison_md
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "experiment.yaml").write_text(Path(exp_path).read_text(encoding="utf-8"), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    (out / "comparison.json").write_text(json.dumps(comparison, indent=1, default=str), encoding="utf-8")
    (out / "comparison.md").write_text(render_comparison_md(comparison), encoding="utf-8")
    (out / "comparison.html").write_text(render_comparison_html(comparison, result), encoding="utf-8")
    paths = write_outputs(result, out / "run")
    return {"comparison": str(out / "comparison.html"), "markdown": str(out / "comparison.md"),
            "manifest": str(out / "manifest.json"), **{f"run_{k}": v for k, v in paths.items()}}


def default_out(name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path(".genai-evals") / "experiments" / re.sub(r"[^A-Za-z0-9_.-]+", "-", name) / stamp


def reproduction_check(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """For each variant x grader: does the new delta fall inside the original CI, and does significance agree?"""
    rows = []
    for v, r in old.get("results", {}).items():
        nr = new.get("results", {}).get(v, {})
        for g, q in r.get("quality", {}).items():
            nq = nr.get("quality", {}).get(g)
            if not nq:
                rows.append({"variant": v, "grader": g, "status": "missing"})
                continue
            inside = q["ci_low"] - 1e-9 <= nq["delta"] <= q["ci_high"] + 1e-9
            rows.append({"variant": v, "grader": g, "old_delta": q["delta"], "old_ci": [q["ci_low"], q["ci_high"]],
                         "new_delta": nq["delta"], "within_ci": inside, "same_significance": q["significant"] == nq["significant"],
                         "status": "reproduced" if inside and q["significant"] == nq["significant"] else "differs"})
        if r.get("verdict") != nr.get("verdict"):
            rows.append({"variant": v, "grader": "(verdict)", "old_delta": None, "new_delta": None,
                         "status": f"verdict {r.get('verdict')} -> {nr.get('verdict')}"})
    return rows


def environment_note() -> str:
    return f"genai-eval-framework {__version__} · Python {sys.version.split()[0]}"
