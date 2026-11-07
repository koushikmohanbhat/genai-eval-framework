import json
from types import SimpleNamespace as NS

from genai_evals.calibration import calibrate, cohen_kappa
from genai_evals.config import ProvidersConfig, SuiteSpec, check_suite, load_cases
from genai_evals.models import Case
from genai_evals.runner import Runner, baseline_from_result, compare_results
from genai_evals.store import ResultStore

from .conftest import MOCK_PROVIDERS, write_jsonl

PROV = ProvidersConfig(**MOCK_PROVIDERS)


def test_suite_requires_explicit_judges():
    s = SuiteSpec(name="s", dataset=[{"id": "a", "input": "x"}], graders=[{"name": "r", "type": "rubric"}],
                  targets=[{"name": "t", "kind": "model", "profile": "echo"}])
    assert any("no default judge" in p for p in check_suite(s, PROV))
    assert check_suite(s.model_copy(update={"judges": ["judge-a", "judge-b"]}), PROV) == []


def test_model_target_end_to_end_with_cache_tokenisation_and_store(tmp_path):
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "my aadhaar is 2345 6789 0124", "reference": {"answer": "aadhaar"}},
                                               {"id": "b", "input": "hello world", "reference": {"answer": "hello world"}, "critical": True}])
    suite = SuiteSpec(name="echo-suite", dataset=str(data), targets=[{"name": "echo", "kind": "model", "profile": "echo", "system": "Repeat: {{ input }}"}],
                      graders=[{"name": "ex", "type": "exact"}, {"name": "pii", "type": "pii_leak"}],
                      gates=[{"name": "privacy", "rule": "mean(pii) >= 1.0"}, {"name": "crit", "rule": "critical_pass()"}], base_dir=str(tmp_path))
    r1 = Runner(suite, PROV, cache_dir=tmp_path / "cache").run()
    assert not r1["passed"]                                            # the echo leaks the Aadhaar number
    assert {g["gate"]: g["passed"] for g in r1["gates"]} == {"privacy": False, "crit": True}
    stored = next(s for s in r1["samples"] if s["case_id"] == "a")
    assert "2345 6789 0124" not in json.dumps(stored) and "<AADHAAR_" in stored["output"]   # tokenised before storage
    assert r1["summary"]["echo"]["ops"]["cost_usd"]["mean"] > 0
    # tokenisation on: no cache by default (it would hold raw outputs) ...
    assert not (tmp_path / "cache").exists()
    r2 = Runner(suite, PROV, cache_dir=tmp_path / "cache").run()
    assert not any(s["metadata"].get("cached") for s in r2["samples"])
    # ... unless the target opts in explicitly; files are then private to the user
    suite.targets[0].options["cache"] = True
    Runner(suite, PROV, cache_dir=tmp_path / "cache").run()
    r3 = Runner(suite, PROV, cache_dir=tmp_path / "cache").run()
    assert all(s["metadata"].get("cached") for s in r3["samples"])
    assert all(oct(p.stat().st_mode)[-3:] == "600" for p in (tmp_path / "cache").iterdir())
    assert r3["data_handling"]["tokenizer"]["type"] == "builtin"
    store = ResultStore(f"sqlite:///{tmp_path}/r.db")
    store.save(r1)
    assert store.history("echo-suite")[0]["run_id"] == r1["run_id"]


class ScriptedProvider:
    """Fake chat client: first call asks for lookup_policy, second answers."""

    def __init__(self):
        self.n = 0

    def converse(self, **kw):
        self.n += 1
        if not any("toolResult" in b for m in kw["messages"] for b in m["content"]):
            return {"output": {"message": {"content": [{"toolUse": {"toolUseId": f"u{self.n}", "name": "lookup_policy",
                                                                    "input": {"policy_number": "MOT-2026-004512"}}}]}},
                    "usage": {"inputTokens": 100, "outputTokens": 10}}
        return {"output": {"message": {"content": [{"text": "Your policy is active."}]}}, "usage": {"inputTokens": 120, "outputTokens": 8}}


def test_tool_loop_agent_on_any_provider(tmp_path, ex_dir):
    prov = ProvidersConfig(profiles={**MOCK_PROVIDERS["profiles"], "bedrock-x": {"provider": "bedrock", "model": "anthropic.claude-x", "retries": 0,
                                                                                 "pricing": {"input_per_mtok": 3, "output_per_mtok": 15}}})
    suite = SuiteSpec(name="agent", dataset=[{"id": "a", "input": "check MOT-2026-004512", "reference": {"tool_calls": [{"name": "lookup_policy"}]}}],
                      prompts_dir=str(ex_dir / "prompts"),
                      targets=[{"name": "bedrock", "kind": "agent", "framework": "tool_loop", "profile": "bedrock-x", "prompt": "claims_agent",
                                "tools_module": "insurance_tools"}],
                      graders=[{"name": "tools", "type": "tool_args_match"}, {"name": "steps", "type": "step_budget", "max_steps": 3}],
                      base_dir=str(ex_dir))
    r = Runner(suite, prov, clients={"bedrock-x": ScriptedProvider()}, use_cache=False).run()
    s = r["samples"][0]
    assert s["trajectory"][0]["name"] == "lookup_policy" and s["trajectory"][0]["result"]["status"] == "active"
    assert s["output"] == "Your policy is active." and s["steps"] == 2 and s["usage"]["input_tokens"] == 220
    assert s["prompt_versions"] == {"claims_agent": "1.3.2"} and s["cost_usd"] > 0
    assert r["passed"] and r["summary"]["bedrock"]["graders"]["tools"]["mean"] == 1.0


def test_target_errors_become_failed_scores(tmp_path):
    suite = SuiteSpec(name="err", dataset=[{"id": "a", "input": "x"}], targets=[{"name": "boom", "kind": "python", "callable": "nonexistent_mod:fn"}],
                      graders=[{"name": "c", "type": "contains", "value": "x"}], gates=[{"name": "g", "rule": "mean(c) >= 1"}])
    r = Runner(suite, PROV, use_cache=False).run()
    assert r["samples"][0]["error"] and r["scores"][0]["passed"] is False and not r["passed"]


def test_baseline_compare_and_prompt_override(ex_dir):
    from genai_evals.config import load_providers, load_suite
    suite = load_suite(ex_dir / "fnol_agent.yaml")
    prov = load_providers(ex_dir / "providers.yaml")
    first = Runner(suite, prov, use_cache=False).run()
    nr = next(g for g in first["gates"] if g["gate"] == "no-regression")
    assert nr["status"] == "invalid" and not first["passed"]          # no baseline: undecidable, so it blocks
    for g in suite.gates:
        g.allow_missing_baseline = True
    base = Runner(suite, prov, use_cache=False).run()
    for g in suite.gates:
        g.allow_missing_baseline = False
    cand = Runner(suite, prov, prompt_overrides={"claims_agent": "1.4.0"}, use_cache=False).run(baseline=baseline_from_result(base))
    assert base["passed"] and not cand["passed"]
    assert cand["prompt_versions"]["fnol-agent"]["claims_agent"] == "1.4.0"
    comp = compare_results(cand, base)["fnol-agent"]
    assert comp["escalated_injury"]["delta"] > 0 and comp["no_claim_on_inactive_policy"]["delta"] < 0
    crit = next(g for g in cand["gates"] if g["gate"] == "critical-cases")
    assert not crit["passed"]


def test_calibration_kappa():
    assert cohen_kappa([(True, True), (False, False)] * 5) == 1.0
    result = {"scores": [{"grader": "f", "case_id": f"c{i}", "target": "t", "passed": i % 2 == 0} for i in range(10)]}
    labels = {(f"c{i}", "*"): (i % 2 == 0) for i in range(10)}
    labels[("c1", "*")] = True
    c = calibrate(result, "f", labels)
    assert c["n"] == 10 and c["accuracy"] == 0.9 and 0.7 < c["kappa"] < 0.9 and c["mismatches"][0]["case_id"] == "c1"


def test_load_cases_csv(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("id,input,tags,critical,ref_answer\na,hello,x|y,true,hi\n")
    cases = load_cases(SuiteSpec(name="s", dataset=str(p)))
    assert cases[0] == Case(id="a", input="hello", tags=["x", "y"], critical=True, reference={"answer": "hi"})


def test_openai_agents_sdk_target_maps_items(monkeypatch, tmp_path):
    import sys
    import types
    fake = types.ModuleType("agents")

    class Runner_:
        @staticmethod
        async def run(agent, inp, max_turns=10):
            items = [NS(type="tool_call_item", raw_item=NS(call_id="c1", name="lookup_policy", arguments='{"policy_number": "X"}')),
                     NS(type="tool_call_output_item", raw_item={"call_id": "c1"}, output={"status": "active"}),
                     NS(type="handoff_output_item", raw_item=None, source_agent=NS(name="triage"), target_agent=NS(name="claims"))]
            return NS(new_items=items, final_output="done", context_wrapper=NS(usage=NS(input_tokens=50, output_tokens=5, requests=2)))
    fake.Runner = Runner_
    monkeypatch.setitem(sys.modules, "agents", fake)
    (tmp_path / "build_agent_mod.py").write_text("def build(case, opts):\n    return object()\n")
    suite = SuiteSpec(name="sdk", dataset=[{"id": "a", "input": "x"}], base_dir=str(tmp_path),
                      targets=[{"name": "sdk", "kind": "agent", "framework": "openai_agents_sdk", "callable": "build_agent_mod:build"}],
                      graders=[{"name": "h", "type": "handoff_to", "agent": "claims"}, {"name": "t", "type": "tool_called", "tool": "lookup_policy"}])
    r = Runner(suite, PROV, use_cache=False).run()
    s = r["samples"][0]
    assert s["trajectory"][0]["result"] == {"status": "active"} and s["trajectory"][1]["kind"] == "handoff" and s["steps"] == 2
    assert r["passed"] or all(sc["passed"] for sc in r["scores"])


def test_scores_and_errors_are_tokenised_before_storage(tmp_path):
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "PAN ABCPE1234F", "reference": {"fields": {"pan": "ABCPE1234F"}}}])
    suite = SuiteSpec(name="t", dataset=str(data), targets=[{"name": "echo", "kind": "model", "profile": "echo", "system": "x"}],
                      graders=[{"name": "f", "type": "field_match"}, {"name": "ex", "type": "exact", "value": "ABCPE1234F"}],
                      base_dir=str(tmp_path))
    r = Runner(suite, PROV, cache_dir=None).run()
    blob = json.dumps({"samples": r["samples"], "scores": r["scores"], "cases": r["cases"]})
    assert "ABCPE1234F" not in blob and "<PAN_" in blob


def test_calibration_uses_ci_lower_bound_and_min_labels():
    from genai_evals.calibration import calibrate, untrusted_graders

    def result(n, agree):
        return {"scores": [{"grader": "f", "case_id": f"c{i}", "target": "t", "passed": (i % 2 == 0) if i < agree else (i % 2 == 1)}
                           for i in range(n)]}

    def labels(n):
        return {(f"c{i}", "*"): i % 2 == 0 for i in range(n)}

    small = calibrate(result(10, 10), "f", labels(10))           # perfect agreement, but only 10 labels
    assert small["kappa"] == 1.0 and any("only 10" in w for w in small["warnings"])
    assert "only 10 human labels" in untrusted_graders({"f": small}, 0.4)["f"]
    noisy = calibrate(result(40, 32), "f", labels(40))           # 80% agreement on 40 labels
    lo, hi = noisy["kappa_ci"]
    assert lo < noisy["kappa"] < hi
    assert "lower bound" in untrusted_graders({"f": noisy}, 0.55).get("f", "")
    good = calibrate(result(200, 190), "f", labels(200))
    assert untrusted_graders({"f": good}, 0.7) == {}
