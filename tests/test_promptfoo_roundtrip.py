"""Promptfoo round trips: what survives, and what doesn't (documented in docs/promptfoo.md; keep the two in step)."""
import json

import pytest
import yaml

from genai_evals.cli import main as cli
from genai_evals.config import load_providers, load_suite
from genai_evals.interop import export_config, import_results_with_cases
from genai_evals.report.writers import writer


@pytest.fixture(scope="module")
def agent_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("pf")
    mp = pytest.MonkeyPatch()
    for k in ("EVAL_TARGETS", "GENAI_EVALS_PROVIDERS", "EVAL_JUDGE_A", "EVAL_JUDGE_B"):
        mp.delenv(k, raising=False)
    mp.chdir(root)
    assert cli(["init", "proj", "--pack", "tool-agent", "--no-run"]) == 0 or (root / "proj").exists()
    proj = root / "proj"
    cli(["run", str(proj / "suite.yaml"), "--out", str(root / "native"), "--no-html"])
    result = json.loads((root / "native/result.json").read_text())
    (root / "pf.json").write_text(writer("promptfoo").render(result))
    cli(["promptfoo", "import", str(root / "pf.json"), "--suite", str(proj / "suite.yaml"), "--out", str(root / "back"), "--no-html"])
    back = json.loads((root / "back/result.json").read_text())
    yield proj, result, back
    mp.undo()


def _scores(r):
    return {(s["case_id"], s["target"], s.get("repeat", 0), s["grader"]): (s["value"], s["passed"])
            for s in r["scores"] if not s.get("skipped")}


def test_results_round_trip_keeps_scores_repeats_tags_and_gates(agent_run):
    _, native, back = agent_run
    assert _scores(back) == _scores(native)                                   # every non-skipped score, per repeat
    assert {s["repeat"] for s in back["samples"]} == {s["repeat"] for s in native["samples"]} == {0, 1, 2}
    nat_cases = {c["id"]: (sorted(c["tags"]), c["critical"]) for c in native["cases"]}
    assert {c["id"]: (sorted(c["tags"]), c["critical"]) for c in back["cases"]} == nat_cases
    key = ("case_id", "target", "repeat")
    ns = {tuple(s[k] for k in key): s for s in native["samples"]}
    for s in back["samples"]:
        n = ns[tuple(s[k] for k in key)]
        assert (s["output"], s["latency_ms"], s["cost_usd"]) == (n["output"], n["latency_ms"], n["cost_usd"])
        assert s["usage"]["input_tokens"] == n["usage"]["input_tokens"]
    gates = lambda r: {(g["gate"], g["target"]): (g["status"], g["values"]) for g in r["gates"]}   # noqa: E731
    assert gates(back) == gates(native)                                       # pass^k, critical cases, means, p95 latency


def test_results_round_trip_drops_what_promptfoo_cannot_hold(agent_run):
    _, native, back = agent_run
    assert any(s.get("skipped") for s in native["scores"]) and not any(s.get("skipped") for s in back["scores"])
    assert any(s["trajectory"] for s in native["samples"]) and not any(s["trajectory"] for s in back["samples"])
    assert not any(s["final_state"] for s in back["samples"])
    judged = [s for s in native["scores"] if "judges" in (s.get("details") or {})]
    assert judged and not any("judges" in (s.get("details") or {}) for s in back["scores"])


def test_config_export_maps_what_it_can_and_says_what_it_cannot(agent_run, tmp_path):
    proj, _, _ = agent_run
    suite = load_suite(proj / "suite.yaml")
    cfg, notes = export_config(suite, load_providers(proj / "providers.yaml"))
    assert cfg["evaluateOptions"] == {"repeat": 3}
    metrics = {a["metric"] for a in cfg["defaultTest"]["assert"]}
    assert {"no_promises", "latency"} <= metrics                              # policy -> llm-rubric, latency -> latency
    assert not {"task_success", "correct_calls", "no_handoff_loop"} & metrics  # trajectory graders stay in genai-evals
    assert any("final_state" in n for n in notes) and any("gate(s) are not exported" in n for n in notes)
    t = next(t for t in cfg["tests"] if t["description"] == "sup-17")
    assert t["metadata"]["critical"] is True and "jailbreak" in t["metadata"]["tags"]
    yaml.safe_dump(cfg)


def test_plain_promptfoo_results_import(tmp_path):
    rows = [{"testCase": {"description": "t1", "metadata": {"tags": ["edge"], "critical": True}}, "vars": {"input": "hi"},
             "provider": "openai:gpt-x", "response": {"output": "hello", "tokenUsage": {"prompt": 3, "completion": 2}},
             "gradingResult": {"componentResults": [{"pass": True, "score": 1, "reason": "ok",
                                                    "assertion": {"type": "icontains", "metric": "greets"}}]}}] * 2
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"results": {"results": rows}}))
    samples, scores, cases = import_results_with_cases(p)
    assert [s.repeat for s in samples] == [0, 1] and samples[0].target == "openai:gpt-x"
    assert {s.grader for s in scores} == {"greets"} and cases["t1"] == {"tags": ["edge"], "critical": True, "input": "hi"}
