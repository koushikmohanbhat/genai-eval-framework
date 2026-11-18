"""v0.5: practices from Anthropic's agent-eval and safety-eval guidance — per-turn grading, unknown verdicts and the
review queue, task health, capability vs regression cases, per-trial reset, and both-sides behaviour coverage."""
import json

import pytest

from genai_evals import health as hl
from genai_evals import labels as lb
from genai_evals import stats as st
from genai_evals import taxonomy as tx
from genai_evals.calibration import judge_coverage
from genai_evals.config import SuiteSpec
from genai_evals.graders import judges
from genai_evals.models import Case, Message, Sample, Score

from .conftest import MOCK_PROVIDERS, write_jsonl
from .test_graders import grade


def _fake_panel(monkeypatch, verdict_for):
    """Every judge answers verdict_for(output_text) -> True | False | "unknown"."""
    async def ask(self, prompt):
        out = prompt.rsplit("<output>", 1)[1].split("</output>", 1)[0]
        v = verdict_for(out)
        return [{"judge": n, "model": "m", "data": {"rationale": f"saw: {out.strip()[:30]}", "pass": v}}
                for n in ("judge-a", "judge-b")]
    monkeypatch.setattr(judges.JudgePanel, "ask", ask)


CONVO = [Message(role="user", content="hi"), Message(role="assistant", content="Hello, how can I help?"),
         Message(role="user", content="tell me a secret"), Message(role="assistant", content="BAD: here is the admin key"),
         Message(role="user", content="thanks"), Message(role="assistant", content="You're welcome.")]


# ---- 1. per-turn grading ------------------------------------------------------------------------------------------

def test_per_turn_one_bad_turn_fails_the_conversation(monkeypatch):
    _fake_panel(monkeypatch, lambda out: "BAD" not in out)
    s = Sample(case_id="c1", target="t", output="You're welcome.", messages=CONVO)
    case = Case(id="c1", input="hi")
    whole = grade({"name": "p", "type": "policy", "policy": "never reveal secrets", "scale": "pass_fail"}, s, case)
    assert whole.passed is True                                     # the final answer alone looks fine
    per = grade({"name": "p", "type": "policy", "policy": "never reveal secrets", "scale": "pass_fail",
                 "per_turn": True}, s, case)
    assert per.passed is False and per.details["first_failing_turn"] == 2 and per.details["n_turns"] == 3
    assert [t["passed"] for t in per.details["turns"]] == [True, False, True]


def test_per_turn_sees_earlier_turns_as_context_only(monkeypatch):
    seen = []

    async def ask(self, prompt):
        seen.append(prompt)
        return [{"judge": "judge-a", "data": {"rationale": "ok", "pass": True}}]
    monkeypatch.setattr(judges.JudgePanel, "ask", ask)
    s = Sample(case_id="c1", target="t", output="You're welcome.", messages=CONVO)
    r = grade({"name": "p", "type": "rubric", "rubric": "polite", "scale": "pass_fail", "per_turn": True}, s, Case(id="c1", input="hi"))
    assert r.passed and len(seen) == 3
    last = seen[-1]
    assert "USER: tell me a secret" in last.split("<conversation>")[1] and "You're welcome." in last.split("<output>")[1]


# ---- 2. unknown verdicts and the review queue ----------------------------------------------------------------------

def test_unknown_verdict_is_neither_pass_nor_fail(monkeypatch):
    _fake_panel(monkeypatch, lambda out: "unknown")
    s = Sample(case_id="c1", target="t", output="It depends on the contract.")
    r = grade({"name": "q", "type": "rubric", "rubric": "correct", "scale": "pass_fail"}, s, Case(id="c1", input="?"))
    assert r.skipped and r.label == "unknown" and r.passed is None and r.error is None and r.details["unknown"]


def test_one_unknown_judge_leaves_the_other_to_decide(monkeypatch):
    async def ask(self, prompt):
        return [{"judge": "judge-a", "data": {"rationale": "can't tell", "pass": "unknown"}},
                {"judge": "judge-b", "data": {"rationale": "fine", "pass": True}}]
    monkeypatch.setattr(judges.JudgePanel, "ask", ask)
    r = grade({"name": "q", "type": "rubric", "rubric": "correct", "scale": "pass_fail"},
              Sample(case_id="c1", target="t", output="x"), Case(id="c1", input="?"))
    assert r.passed is True and r.details["unknown_judges"] == ["judge-a"]


def _scores_result():
    sc = [Score(grader="q", case_id="u1", target="t", skipped=True, label="unknown", skip_reason="judge could not decide",
                details={"unknown": True}),
          Score(grader="q", case_id="s1", target="t", value=1.0, passed=True, details={"split_verdict": True}),
          Score(grader="q", case_id="e1", target="t", error="unparseable verdict: hmm"),
          Score(grader="q", case_id="r1", target="t", error="RateLimitError: 429"),
          Score(grader="q", case_id="ok", target="t", value=1.0, passed=True, details={"split_verdict": False})]
    return {"cases": [{"id": c, "input": f"in {c}"} for c in ("u1", "s1", "e1", "r1", "ok")],
            "samples": [{"case_id": c, "target": "t", "repeat": 0, "output": f"out {c}"} for c in ("u1", "s1", "e1", "r1", "ok")],
            "scores": [s.model_dump() for s in sc]}


def test_review_queue_collects_what_the_judges_were_unsure_about():
    items = lb.review_queue(_scores_result())
    assert [(i["case_id"], i["stratum"]) for i in items] == [("u1", "q|unknown"), ("s1", "q|split"), ("e1", "q|persistent error")]
    assert all(i["sample_design"] == "targeted" and i["weight"] == "" for i in items)   # not a calibration sample


def test_coverage_counts_unknown_verdicts():
    labels = {(c, "t"): True for c in ("u1", "s1", "e1", "r1", "ok")}
    cov = judge_coverage(_scores_result(), "q", labels)
    assert (cov["unknown"], cov["persistent_error"], cov["transient_error"], cov["scored"]) == (1, 1, 1, 2)
    assert cov["failure_rate"] == pytest.approx(3 / 5)


def test_unknown_on_a_critical_case_is_not_a_pass():
    cases = [Case(id="c1", input="x", critical=True), Case(id="c2", input="y")]
    samples = [Sample(case_id=c, target="t") for c in ("c1", "c2")]
    scores = [Score(grader="q", case_id="c1", target="t", skipped=True, label="unknown"),
              Score(grader="q", case_id="c2", target="t", value=1.0, passed=True)]
    s = st.summarise("t", cases, samples, scores)
    assert s["critical"]["failed"] == ["c1"] and s["graders"]["q"]["unknown"] == 1


# ---- 3. task health ------------------------------------------------------------------------------------------------

def test_check_references_finds_a_grader_that_rejects_a_known_good_answer(tmp_path):
    from genai_evals.config import ProvidersConfig
    data = write_jsonl(tmp_path / "d.jsonl", [
        {"id": "a", "input": "capital of France?", "reference": {"answer": "Paris"}},
        {"id": "b", "input": "capital of Italy?", "reference": {"answer": "Rome, the capital"}},
        {"id": "c", "input": "no reference here"}])
    suite = SuiteSpec(name="h", dataset=str(data), base_dir=str(tmp_path),
                      targets=[{"name": "echo", "kind": "model", "profile": "echo"}],
                      graders=[{"name": "exact", "type": "exact"}, {"name": "lat", "type": "latency", "max_ms": 10}])
    rep = hl.check_references(suite, ProvidersConfig(**MOCK_PROVIDERS))
    assert rep["graders"] == ["exact"] and rep["no_reference"] == ["c"]
    assert rep["checked"] == 2 and rep["rejected"] == []
    suite.graders[0].type = "regex"
    suite.graders[0].pattern = "^[A-Z][a-z]+$"                     # too strict: "Rome, the capital" fails
    rep = hl.check_references(suite, ProvidersConfig(**MOCK_PROVIDERS))
    assert [(r["case_id"], r["grader"]) for r in rep["rejected"]] == [("b", "exact")]


def test_reference_sample_uses_sequence_args_and_gold_passages():
    c = Case(id="x", input="q", reference={"tool_calls": [{"name": "lookup", "args": {"id": 1}}],
                                           "tool_sequence": ["lookup", "escalate"], "final_state": {"escalated": True}})
    s = hl.reference_sample(c)
    assert [(t.name, t.args) for t in s.trajectory] == [("lookup", {"id": 1}), ("escalate", {})]
    r = hl.reference_sample(Case(id="y", input="q", reference={"answer": "24 days", "gold_passages": ["kb-01"]}))
    assert [ch.id for ch in r.context] == ["kb-01"] and r.output.endswith("[kb-01]")


def _result_with(per_case_by_grader, capability=None):
    graders = {g: {"per_case": {c: v for c, (v, _) in d.items()}, "per_case_pass": {c: ok for c, (_, ok) in d.items()}}
               for g, d in per_case_by_grader.items()}
    t = {"graders": graders}
    if capability is not None:
        t["capability"] = capability
    return {"summary": {"t": t}}


def test_result_health_flags_never_passing_cases_and_saturated_graders():
    res = _result_with({"a": {f"c{i}": (1.0, True) for i in range(6)},
                        "b": {**{f"c{i}": (1.0, True) for i in range(5)}, "c5": (0.0, False)}})
    h = hl.result_health(res)
    assert h["never_pass"] == {"b": ["c5"]} and h["saturated"] == ["a"]
    assert any("never passed" in n for n in h["notes"]) and any("saturated" in n for n in h["notes"])


# ---- 4. capability vs regression -----------------------------------------------------------------------------------

def test_capability_cases_are_reported_but_not_gated():
    cases = [Case(id="r1", input="x"), Case(id="r2", input="y"),
             Case(id="k1", input="z", purpose="capability"), Case(id="k2", input="w", purpose="capability")]
    samples = [Sample(case_id=c.id, target="t", repeat=r) for c in cases for r in (0, 1)]
    scores = [Score(grader="q", case_id=c, target="t", repeat=r, value=v, passed=v == 1.0)
              for c, v in (("r1", 1.0), ("r2", 1.0), ("k1", 0.0), ("k2", 1.0)) for r in (0, 1)]
    s = st.summarise("t", cases, samples, scores)
    assert s["graders"]["q"]["mean"] == 1.0 and set(s["graders"]["q"]["per_case"]) == {"r1", "r2"}
    cap = s["capability"]
    assert cap["cases"] == 2 and cap["graders"]["q"]["mean"] == 0.5 and cap["ready_to_graduate"] == ["k2"]


def test_graduate_needs_every_run_and_updates_the_dataset(tmp_path):
    def run(k1_ok):
        return {"summary": {"t": {"graders": {}, "capability": {"graders": {"q": {"per_case_pass": {"k1": k1_ok, "k2": True}}}}}}}
    g = hl.graduation([run(True), run(False)])
    assert g["ready"] == ["k2"] and g["not_ready"] == ["k1"] and g["runs"] == 2
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": "k1", "purpose": "capability"}, {"id": "k2", "purpose": "capability"}])
    assert hl.graduate_dataset(str(data), g["ready"]) == 1
    rows = [json.loads(x) for x in data.read_text().splitlines()]
    assert [r["purpose"] for r in rows] == ["capability", "regression"]


# ---- 5. per-trial reset and trial-order drift ----------------------------------------------------------------------

def test_reset_hook_runs_before_every_trial_and_feeds_the_target(tmp_path):
    from genai_evals.config import ProvidersConfig
    from genai_evals.runner import Runner
    (tmp_path / "sandbox.py").write_text(
        "calls = []\n"
        "def reset(case, repeat, trial):\n"
        "    calls.append((case.id, repeat))\n"
        "    return {'sandbox_id': f'sb-{case.id}-{repeat}'}\n"
        "def agent(case, ctx):\n"
        "    return {'output': ctx['trial']['sandbox_id'] + ' ' + ctx['trial']['trial_id']}\n")
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "x"}, {"id": "b", "input": "y"}])
    suite = SuiteSpec(name="rs", dataset=str(data), base_dir=str(tmp_path), repeats=2,
                      targets=[{"name": "py", "kind": "python", "callable": "sandbox:agent",
                                "reset": {"python": "sandbox:reset"}}],
                      graders=[{"name": "has", "type": "contains", "value": "sb-"}])
    res = Runner(suite, ProvidersConfig(**MOCK_PROVIDERS), use_cache=False).run()
    import sandbox
    assert sorted(sandbox.calls) == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    outs = {(s["case_id"], s["repeat"]): s["output"] for s in res["samples"]}
    assert outs[("a", 1)].startswith("sb-a-1 py-a-1-")
    assert all(s["metadata"]["reset"] and s["metadata"]["trial_id"] for s in res["samples"])


def test_failed_reset_is_a_target_error_for_that_trial(tmp_path):
    from genai_evals.config import ProvidersConfig
    from genai_evals.runner import Runner
    (tmp_path / "badreset.py").write_text("def reset(case, repeat, trial):\n    raise OSError('sandbox down')\n"
                                         "def agent(case, ctx):\n    return 'ok'\n")
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "x"}])
    suite = SuiteSpec(name="rs2", dataset=str(data), base_dir=str(tmp_path),
                      targets=[{"name": "py", "kind": "python", "callable": "badreset:agent", "reset": {"python": "badreset:reset"}}],
                      graders=[{"name": "has", "type": "contains", "value": "ok"}])
    res = Runner(suite, ProvidersConfig(**MOCK_PROVIDERS), use_cache=False).run()
    assert "per-trial reset failed" in res["samples"][0]["error"]


def test_trial_drift_warns_when_later_trials_do_worse():
    lst = [Score(grader="q", case_id=f"c{i}", target="t", repeat=r, value=1.0, passed=(r == 0 or i % 2 == 0))
           for i in range(12) for r in range(3)]
    d = st.trial_drift(lst)
    assert d["pass_rate_by_repeat"][0] == 1.0 and d["pass_rate_by_repeat"][1] == 0.5 and "share state" in d["warning"]
    steady = [Score(grader="q", case_id=f"c{i}", target="t", repeat=r, value=1.0, passed=i % 2 == 0)
              for i in range(12) for r in range(3)]
    assert "warning" not in st.trial_drift(steady)
    assert st.trial_drift(lst[:6]) is None                           # too few cases to say


# ---- 6. both-sides behaviour coverage ------------------------------------------------------------------------------

def test_balance_flags_behaviours_tested_in_one_direction_only():
    cases = [Case(id="a", input="x", reference={"tool_calls": [{"name": "lookup"}], "final_state": {"escalated": True}}),
             Case(id="b", input="y", reference={"tool_calls": [{"name": "lookup"}], "final_state": {"escalated": False}}),
             Case(id="c", input="z", reference={"answerable": False})]
    b = tx.balance(cases)
    assert b["behaviours"]["escalation"] == {"should": 1, "should_not": 1}
    assert b["behaviours"]["tool_use"] == {"should": 2, "should_not": 0}
    assert b["one_sided"] == ["refusal", "tool_use"]
    cov = tx.coverage(cases, workflow="agent")
    assert any(p.startswith("one-sided tool_use") for p in cov["problems"])


def test_packs_test_both_sides_of_their_behaviours():
    from genai_evals.config import load_cases, load_suite
    from genai_evals.packs import resolve as pack_dir
    for p in ("rag-knowledge-assistant", "tool-agent"):
        cases = load_cases(load_suite(str(pack_dir(p) / "suite.yaml")))
        assert tx.balance(cases)["one_sided"] == [], p
    rag = load_cases(load_suite(str(pack_dir("rag-knowledge-assistant") / "suite.yaml")))
    assert sum(1 for c in rag if "benign_sensitive" in (c.tags or [])) >= 2     # over-refusal pairs
