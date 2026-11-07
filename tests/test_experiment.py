import json
import shutil

import pytest

from genai_evals.cli import main as cli
from genai_evals.comparison import compare_pair
from genai_evals.config import load_providers
from genai_evals.experiment import build_suite, load_experiment, reproduction_check, run_experiment, verify_manifest, write_experiment
from genai_evals.runner import SuiteError


@pytest.fixture
def work(tmp_path, ex_dir, monkeypatch):
    w = tmp_path / "ex"
    shutil.copytree(ex_dir, w, ignore=shutil.ignore_patterns(".genai-evals", "calibration.json", "__pycache__"))
    monkeypatch.chdir(w)
    return w


def test_experiment_compares_variants_on_the_same_cases(work):
    result, comp, manifest = run_experiment(work / "fnol_experiment.yaml", use_cache=False)
    assert set(result["summary"]) == {"prod-1.3.2", "prompt-1.4.0", "fast-model"}
    # every variant graded on exactly the same cases
    ids = {t: set(s["graders"]["task_success"]["per_case"]) for t, s in result["summary"].items()}
    assert ids["prod-1.3.2"] == ids["prompt-1.4.0"] == ids["fast-model"]

    p14 = comp["results"]["prompt-1.4.0"]
    assert p14["verdict"] == "regresses" and len(p14["critical"]["new_failures"]) == 4
    assert "escalated_injury" in p14["improvements"] and "verified_policy" in p14["regressions"]
    assert p14["quality"]["task_success"]["delta"] == 0 and len(p14["quality"]["task_success"]["fixed"]) == 4
    assert {f["grader"] for f in p14["failure_types"]} >= {"verified_policy", "escalated_injury"}
    assert any(fc["critical"] for fc in p14["flipped_cases"])

    fast = comp["results"]["fast-model"]
    assert fast["ops"]["cost_usd"]["relative"] < -0.5 and fast["ops"]["latency_ms"]["relative"] < -0.4
    assert fast["ops"]["cost_usd"]["delta"] < 0
    assert comp["variants"][1]["changes"] == {"options": {"model": "fast"}}

    # Bonferroni over 2 variants x (7 graders + cost + latency)
    assert comp["tests"] == 18 and comp["alpha_per_test"] == pytest.approx(0.05 / 18)
    assert comp["dropped_gates"] == ["no-regression"]

    assert manifest["prompts"].keys() == {"claims_agent@1.3.2", "claims_agent@1.4.0"}
    assert {"experiment", "suite", "dataset", "code:insurance_targets"} <= set(manifest["files"])
    assert manifest["run"]["seed"] == 7 and manifest["run"]["repeats"] == 3
    assert all("auth" not in p for p in manifest["providers_used"].values())


def test_experiment_outputs_rerun_and_tamper_detection(work, capsys):
    rc = cli(["experiment", "run", "fnol_experiment.yaml", "--out", "out/e1", "--no-cache"])
    assert rc == 0
    for f in ("manifest.json", "experiment.yaml", "comparison.json", "comparison.md", "comparison.html", "run/report.html"):
        assert (work / "out/e1" / f).exists(), f
    html = (work / "out/e1/comparison.html").read_text()
    assert "prompt-1.4.0" in html and "regresses" in html and "<script" not in html
    assert cli(["experiment", "run", "fnol_experiment.yaml", "--out", "out/e0", "--no-cache", "--fail-on-regression"]) == 1

    assert cli(["experiment", "rerun", "out/e1", "--out", "out/e2"]) == 0
    rows = json.loads((work / "out/e2/reproduction.json").read_text())
    assert rows and all(r["status"] == "reproduced" for r in rows)

    (work / "prompts/claims_agent/1.4.0/system.md.j2").write_text("edited\n", encoding="utf-8")
    capsys.readouterr()
    assert cli(["experiment", "rerun", "out/e1", "--out", "out/e3"]) == 2
    assert "prompt claims_agent@1.4.0 changed" in capsys.readouterr().err
    m = json.loads((work / "out/e1/manifest.json").read_text())
    exp = load_experiment(work / "fnol_experiment.yaml")
    suite, _, _ = build_suite(exp, load_providers(work / "providers.yaml"))
    assert verify_manifest(m, suite) == ["prompt claims_agent@1.4.0 changed"]


def test_build_suite_validation(work):
    import yaml

    def write_yaml(path, data):
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    write_yaml(work / "bad.yaml", {"name": "x", "suite": "fnol_agent.yaml", "target": "nope", "variants": [{"name": "a"}]})
    with pytest.raises(SuiteError):
        build_suite(load_experiment(work / "bad.yaml"), load_providers(work / "providers.yaml"))
    write_yaml(work / "dup.yaml", {"name": "x", "suite": "fnol_agent.yaml", "baseline": {"name": "a"}, "variants": [{"name": "a"}]})
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="unique"):
        load_experiment(work / "dup.yaml")
    write_yaml(work / "multi.yaml", {"name": "x", "suite": "claims_extraction.yaml", "variants": [{"name": "a"}]})
    with pytest.raises(SuiteError, match="set `target:`"):
        build_suite(load_experiment(work / "multi.yaml"), load_providers(work / "providers.yaml"))


def _summ(target, per_case, passed, tags_ok=True, cost=None, crit_failed=()):
    return {"target": target, "graders": {"g": {"mean": sum(per_case.values()) / len(per_case), "pass_hat_k": None,
                                                 "per_case": per_case, "per_case_pass": passed}},
            "ops": {"cost_usd": {"mean": cost}, "latency_ms": {"mean": None}, "errors": 0},
            "critical": {"total": 1, "failed": list(crit_failed)}}


def test_compare_pair_slices_min_n_and_verdicts():
    cases = [{"id": f"c{i}", "tags": ["big"] if i < 8 else ["small"]} for i in range(10)]
    base = _summ("b", {f"c{i}": 1.0 for i in range(10)}, {f"c{i}": True for i in range(10)}, cost=0.01)
    cand_pc = {f"c{i}": (0.0 if i < 8 else 1.0) for i in range(10)}
    cand = _summ("c", cand_pc, {k: v == 1.0 for k, v in cand_pc.items()}, cost=0.005)
    r = compare_pair(base, cand, cases=cases, min_n=5)
    assert r["verdict"] == "regresses" and r["regressions"] == ["g"]
    assert [s["slice"] for s in r["slices"]] == ["big"] and r["slices_below_min_n"] == []
    assert len(r["quality"]["g"]["new_failures"]) == 8 and r["ops"]["cost_usd"]["relative"] == pytest.approx(-0.5)
    # identical -> no detectable difference; a new critical failure alone -> regresses
    same = compare_pair(base, base, cases=cases)
    assert same["verdict"] == "no detectable difference" and same["flipped_cases"] == []
    crit = compare_pair(base, {**base, "target": "c", "critical": {"total": 1, "failed": ["c1"]}}, cases=cases)
    assert crit["verdict"] == "regresses"


def test_reproduction_check_flags_differences():
    old = {"results": {"v": {"verdict": "improves", "quality": {"g": {"delta": 0.2, "ci_low": 0.1, "ci_high": 0.3, "significant": True}}}}}
    new_ok = {"results": {"v": {"verdict": "improves", "quality": {"g": {"delta": 0.25, "significant": True}}}}}
    new_bad = {"results": {"v": {"verdict": "no detectable difference", "quality": {"g": {"delta": 0.0, "significant": False}}}}}
    assert [r["status"] for r in reproduction_check(old, new_ok)] == ["reproduced"]
    assert {r["status"] for r in reproduction_check(old, new_bad)} == {"differs", "verdict improves -> no detectable difference"}


def test_compare_cli_across_runs(work, tmp_path):
    assert cli(["run", "fnol_agent.yaml", "--out", "out/a", "--no-cache", "--allow-missing-baseline"]) == 0
    cli(["run", "fnol_agent.yaml", "--prompt", "claims_agent=1.4.0", "--out", "out/b", "--no-cache", "--allow-missing-baseline"])
    assert cli(["compare", "out/b/result.json", "out/a/result.json", "--out", "out/cmp"]) == 0
    comp = json.loads((work / "out/cmp/comparison.json").read_text())
    assert comp["results"]["fnol-agent"]["verdict"] == "regresses"
    assert cli(["compare", "out/b/result.json", "out/a/result.json", "--fail-on-regression"]) == 1
    _ = write_experiment  # exported API
