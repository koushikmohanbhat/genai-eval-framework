"""v0.4: alignment with OpenAI's evaluation best practices (judges, human labels, coverage, growing the eval set)."""
import json
import re

import pytest

from genai_evals import calibration as cal
from genai_evals import cases as cs
from genai_evals import labels as lb
from genai_evals import taxonomy as tx
from genai_evals.cli import main as cli
from genai_evals.config import ProvidersConfig, SuiteSpec, load_cases
from genai_evals.graders import judges
from genai_evals.models import Case, ContextChunk, Sample, Score, ToolCall
from genai_evals.stats import summarise

from .conftest import MOCK_PROVIDERS, write_jsonl
from .test_graders import S, ctx, grade

# ---- judges ------------------------------------------------------------------------------------------------------

def test_judge_prompts_ask_for_a_short_checkable_rationale_before_the_verdict():
    import inspect
    src = inspect.getsource(judges.Rubric.grade)               # the rubric schema is filled in per scale
    assert '{"rationale": "...", "pass": true|false}' in src and '{"rationale": "...", "score": <1-5>}' in src
    for name in ("RELEVANCE_PROMPT", "PAIRWISE_PROMPT", "REFUSAL_PROMPT"):
        tmpl = getattr(judges, name)
        schema = tmpl.rsplit("Respond with JSON only:", 1)[1]
        assert schema.index("rationale") < min(schema.index(k) for k in ("score", "winner", "refused") if k in schema), name
    for name in ("RUBRIC_PROMPT", "RELEVANCE_PROMPT", "PAIRWISE_PROMPT"):
        assert "{rules}" in getattr(judges, name)
    rules = judges.JUDGE_RULES
    assert "not better because it is longer" in rules and "BEFORE" in rules
    assert "two short sentences" in rules and "Do not write out your step-by-step thinking" in rules
    assert "personal details" in rules
    assert "at most 20 words" in judges.FAITH_PROMPT and "at most 20 words" in judges.CONTEXT_RECALL_PROMPT


def test_stored_rationale_is_capped_or_dropped():
    long = {"rationale": "x " * 400, "score": 5}
    assert len(judges._rationale(long, {})) <= judges.DEFAULT_MAX_RATIONALE
    assert len(judges._rationale(long, {"max_rationale_chars": 50})) <= 50
    assert judges._rationale(long, {"store_rationale": False}) is None
    assert judges._rationale({"reasoning": "old key"}, {}) == "old key"
    case = Case(id="c1", input="q", reference={"answer": "inform within 48 hours"})
    c = ctx(store_rationale=False)
    sc = grade({"name": "r", "type": "rubric", "use_reference": True}, Sample(**S, output="inform within 48 hours"), case, c)
    assert sc.passed and not sc.reasoning and all(j["reasoning"] is None for j in sc.details["judges"])
    for name in ("FAITH_PROMPT", "CONTEXT_RECALL_PROMPT"):
        t = getattr(judges, name)
        assert t.index('"evidence"') < t.index('"verdict"' if name == "FAITH_PROMPT" else '"attributed"')


def test_pairwise_records_length_and_summary_reports_length_bias():
    c = ctx()
    case = Case(id="c1", input="q", reference={"answer": "inform within 48 hours with documents"})
    a = Sample(case_id="c1", target="a", output="Inform within 48 hours with documents, " + "and so on " * 20)
    b = Sample(case_id="c1", target="b", output="Call us.")
    c.samples = {("a", "c1", 0): a, ("b", "c1", 0): b}
    sc = grade({"name": "pw", "type": "pairwise", "against": "b", "use_reference": True, "max_length_ratio": 3}, a, case, c)
    assert sc.value == 1.0 and sc.details["longer_won"] is True and sc.details["length_confounded"] is True
    assert sc.details["length_words"][0] > sc.details["length_words"][1]
    summ = summarise("a", [case], [a], [sc])
    lb_ = summ["graders"]["pw"]["length_bias"]
    assert lb_["longer_win_rate"] == 1.0 and lb_["decided"] == 1 and lb_["length_confounded"] == 1
    assert lb_["position_consistency"] == 1.0


def test_judge_prompt_hash_covers_the_rules(monkeypatch):
    h = judges.judge_prompts_hash()
    monkeypatch.setattr(judges, "JUDGE_RULES", judges.JUDGE_RULES + " extra")
    assert judges.judge_prompts_hash() != h


# ---- RAG context graders ---------------------------------------------------------------------------------------

def test_context_precision_is_rank_weighted():
    case = Case(id="c1", input="How long is the password reset link valid?", reference={"answer": "the reset link expires after 30 minutes"})
    good = ContextChunk(id="kb-02", text="The password reset link expires after 30 minutes.")
    bad = ContextChunk(id="kb-09", text="Employees may work from home two days a week.")
    first = grade({"name": "cp", "type": "context_precision"}, Sample(**S, output="x", context=[good, bad]), case)
    later = grade({"name": "cp", "type": "context_precision"}, Sample(**S, output="x", context=[bad, good]), case)
    assert first.value == pytest.approx(1.0) and later.value == pytest.approx(0.5)
    assert first.details["useful"] == ["kb-02"]
    assert grade({"name": "cp", "type": "context_precision"}, Sample(**S, output="x"), case).skipped


def test_context_recall_needs_reference_and_counts_supported_statements():
    case = Case(id="c1", input="leave?", reference={"answer": "Employees get 24 days of paid annual leave. Unused days expire at year end."})
    ctx_ = [ContextChunk(id="kb-01", text="Employees get 24 days of paid annual leave per calendar year.")]
    sc = grade({"name": "cr", "type": "context_recall"}, Sample(**S, output="x", context=ctx_), case)
    assert sc.value == pytest.approx(0.5) and sc.details["missing"]
    assert grade({"name": "cr", "type": "context_recall"}, Sample(**S, output="x", context=ctx_), Case(id="c1", input="q")).skipped
    assert grade({"name": "cr", "type": "context_recall"}, Sample(**S, output="x"), case).value == 0.0


# ---- agents -------------------------------------------------------------------------------------------------------

def test_no_handoff_loop():
    case = Case(id="c1", input="x")

    def run(path, **p):
        traj = [ToolCall(name=f"to_{a}", kind="handoff", args={"to": a}) for a in path]
        return grade({"name": "h", "type": "no_handoff_loop", "start_agent": "triage", **p}, Sample(**S, trajectory=traj), case)
    assert run(["billing", "refunds"]).passed
    loop = run(["billing", "triage"])
    assert not loop.passed and "billing -> triage" in loop.reasoning
    assert run(["billing", "triage"], allow_return=True).passed
    assert not run(["a", "b", "c", "d", "e"], max_handoffs=4).passed
    assert grade({"name": "h", "type": "no_handoff_loop"}, Sample(**S), case).skipped


# ---- human labels -------------------------------------------------------------------------------------------------

def _reviews(tmp_path, rows):
    p = tmp_path / "reviews.csv"
    p.write_text("case_id,target,reviewer,round,label,adjudicated\n" + "".join(",".join(map(str, r)) + "\n" for r in rows))
    return p


def test_consensus_majority_unanimous_and_adjudication(tmp_path):
    p = _reviews(tmp_path, [
        ("c1", "*", "ana", 1, "pass", ""), ("c1", "*", "bo", 1, "pass", ""), ("c1", "*", "cy", 1, "fail", ""),
        ("c2", "*", "ana", 1, "pass", ""), ("c2", "*", "bo", 1, "fail", ""),
        ("c3", "*", "ana", 1, "fail", ""), ("c3", "*", "bo", 1, "pass", ""), ("c3", "*", "lead", 1, "fail", "true"),
        ("c4", "*", "ana", 1, "fail", ""), ("c4", "*", "ana", 2, "pass", ""), ("c4", "*", "bo", 1, "pass", ""),
        ("c5", "*", "ana", 1, "unsure", ""),
    ])
    rv = cal.read_reviews(p)
    assert "c5" not in {k[0] for k in rv}
    labels, disputed = cal.consensus(rv)
    assert labels[("c1", "*")] is True and labels[("c3", "*")] is False and labels[("c4", "*")] is True
    assert [d["case_id"] for d in disputed] == ["c2"]
    un, und = cal.consensus(rv, "unanimous")
    assert ("c1", "*") not in un and {d["case_id"] for d in und} == {"c1", "c2"}
    ag = cal.reviewer_agreement(rv)
    assert ag["multi_reviewed"] == 4 and ag["reviewers"]["ana"]["n"] == 3 and ag["disputed"]
    assert cal.read_labels(p) == labels


def test_fleiss_kappa_known_values():
    assert cal.fleiss_kappa([[True, True], [False, False], [True, True], [False, False]]) == pytest.approx(1.0)
    k = cal.fleiss_kappa([[True, False], [True, True], [False, False], [True, False]])
    assert -1 <= k < 0.5
    assert cal.fleiss_kappa([[True]]) is None


def _result(grader_verdicts: dict[str, list[bool]], n: int):
    scores = []
    for g, vs in grader_verdicts.items():
        for i, v in enumerate(vs[:n]):
            scores.append({"grader": g, "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": v, "value": float(v),
                           "cost_usd": 0.01 if g == "strong" else 0.001})
    return {"scores": scores}


def test_compare_judges_ladder():
    n = 200                                              # non-inferiority needs a decent label set
    human = [i % 3 != 0 for i in range(n)]
    labels = {(f"c{i}", "*"): h for i, h in enumerate(human)}
    strong = [not h if i % 50 == 5 else h for i, h in enumerate(human)]        # 2% disagreement with humans
    cheap_ok = [not h if i % 50 == 1 else h for i, h in enumerate(human)]      # also 2%, on other items
    cheap_bad = [not h if i % 4 == 0 else h for i, h in enumerate(human)]      # 25%
    ok = cal.compare_judges((_result({"cheap": cheap_ok}, n), "cheap"), (_result({"strong": strong}, n), "strong"), labels, margin=0.1)
    assert ok["approved"] and ok["n"] == n and ok["judge_cost_cheap"] < ok["judge_cost_strong"]
    bad = cal.compare_judges((_result({"cheap": cheap_bad}, n), "cheap"), (_result({"strong": strong}, n), "strong"), labels)
    assert not bad["approved"] and "may be worse" in bad["reason"]
    few = cal.compare_judges((_result({"cheap": cheap_ok}, 10), "cheap"), (_result({"strong": strong}, 10), "strong"), labels)
    assert not few["approved"] and "shared labelled units" in few["reason"]
    assert ok["basis"].startswith("calibration sample (design unknown)")


def test_calibrate_reports_reviewer_agreement(tmp_path):
    res = _result({"j": [True, False, True, True]}, 4)
    rows = [(f"c{i}", "*", r, 1, lab, "") for i, lab in enumerate(["pass", "fail", "pass", "fail"]) for r in ("ana", "bo")]
    rv = cal.read_reviews(_reviews(tmp_path, rows))
    out = cal.calibrate(res, "j", cal.consensus(rv)[0], cal.reviewer_agreement(rv))
    assert out["human_agreement"]["fleiss_kappa"] == pytest.approx(1.0) and out["n"] == 4


def test_labelling_sheet_and_guide(tmp_path):
    cases = [{"id": f"c{i}", "tags": ["leave" if i % 2 else "it"], "input": f"q{i}"} for i in range(20)]
    samples = [{"case_id": f"c{i}", "target": "t", "repeat": 0, "input": f"q{i}", "output": f"a{i}"} for i in range(20)]
    scores = [{"grader": "j", "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": i % 5 != 0} for i in range(20)]
    items = lb.sample_items({"cases": cases, "samples": samples, "scores": scores}, "j", n=8)
    assert len(items) == 8 and {i["tags"] for i in items} == {"leave", "it"}
    assert all(i["judge_passed"] == "" for i in items)                   # reviewers don't see the judge's verdict
    f = lb.write_sheet(items, tmp_path / "sheet.csv", ["ana", "bo"])
    assert len(f.read_text().splitlines()) == 1 + 16
    suite = SuiteSpec(name="s", graders=[{"name": "q", "type": "rubric", "rubric": "Correct and polite.",
                                          "anchors": {"5": "fully correct", "1": "wrong"}}])
    guide = lb.labelling_guide(suite, "q")
    assert "### 5" in guide and "fully correct" in guide and "Correct and polite." in guide and "adjudicat" in guide
    with pytest.raises(ValueError):
        lb.labelling_guide(suite, "nope")


# ---- coverage and growing the eval set ------------------------------------------------------------------------------

def test_coverage_by_workflow():
    cases = [Case(id="a", input="x", tags=["typical"]), Case(id="b", input="x", tags=["edge", "typos"]),
             Case(id="c", input="x", tags=["adversarial", "jailbreak"]), Case(id="d", input="x")]
    cov = tx.coverage(cases, "agent")
    assert cov["untagged"] == 1 and cov["stress"] == 2 and cov["representative"] == 2
    assert cov["thin"]["typos"] == {"cases": 1, "needed": 5, "tier": "standard"} and cov["required"]["jailbreak"] == 10
    assert "typos" in tx.coverage(cases, "agent", min_per_tag=1)["present"]
    assert any("representative set too small (2 < 30)" in p for p in cov["problems"])
    assert "circular_handoff" in cov["missing"] and "no_answer" not in cov["missing"]
    assert "no_answer" in tx.coverage(cases, "rag")["missing"] and "circular_handoff" not in tx.coverage(cases, "rag")["missing"]
    assert tx.workflow_of(SuiteSpec(name="s", pack="tool-agent@0.1.0")) == "agent"


def test_drafts_are_left_out_until_reviewed(tmp_path):
    write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "x"}, {"id": "b", "input": "y", "review_status": "draft"},
                                       {"id": "c", "input": "z", "review_status": "reviewed"}])
    assert [c.id for c in load_cases(SuiteSpec(name="s", dataset="d.jsonl", base_dir=str(tmp_path)))] == ["a", "c"]
    assert len(load_cases(SuiteSpec(name="s", dataset="d.jsonl", base_dir=str(tmp_path), include_draft_cases=True))) == 3


def test_harvest_tokenises_and_promote_requires_review(tmp_path):
    result = {"run_id": "run_1",
              "samples": [{"case_id": "p1", "target": "prod", "input": "Call me on 9876543210 about ORD-1", "output": "ok"},
                          {"case_id": "p2", "target": "prod", "input": "fine", "output": "ok"},
                          {"case_id": "p3", "target": "prod", "input": "Call me on 9876543210 about ORD-1", "output": "ok"}],
              "scores": [{"grader": "g", "case_id": "p1", "target": "prod", "passed": False, "value": 0.2},
                         {"grader": "g", "case_id": "p2", "target": "prod", "passed": True, "value": 0.9},
                         {"grader": "g", "case_id": "p3", "target": "prod", "passed": False, "value": 0.1}]}
    rows = cs.harvest(result, tokenizer=cs.default_tokenizer())
    assert len(rows) == 1 and "9876543210" not in rows[0]["input"] and rows[0]["review_status"] == "draft"
    assert rows[0]["tags"] == ["harvested", "failed:g"] and rows[0]["metadata"]["original_case_id"] == "p1"
    assert len(cs.harvest(result, below=0.95)) == 2
    drafts = cs.write_jsonl(rows, tmp_path / "drafts.jsonl")
    data = cs.write_jsonl([{"id": "x", "input": "q"}], tmp_path / "data.jsonl")
    done, refused = cs.promote(drafts, data)
    assert not done and "no reference" in refused[0]
    rows[0]["reference"] = {"answer": "escalate"}
    cs.write_jsonl(rows, drafts)
    done, _ = cs.promote(drafts, data, reviewer="ana")
    got = cs.read_jsonl(data)
    assert done == [rows[0]["id"]] and got[-1]["review_status"] == "reviewed" and "edge" in got[-1]["tags"]
    assert got[-1]["metadata"]["reviewed_by"] == "ana" and "output" not in got[-1]["metadata"]
    assert cs.read_jsonl(drafts) == []


def test_generate_drafts_for_missing_tags():
    suite = SuiteSpec(name="support", description="order support agent", pack="tool-agent@0.1.0")
    existing = [Case(id="a", input="Where is ORD-1?", tags=["typical", "edge", "adversarial", "multilingual"])]
    rows = cs.generate(suite, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", tags=["typos", "jailbreak"], n=2, existing=existing)
    assert len(rows) == 4 and {r["tags"][0] for r in rows} == {"typos", "jailbreak"}
    assert all(r["review_status"] == "draft" and r["source"] == "synthetic" and "generated" in r["tags"] for r in rows)
    auto = cs.generate(suite, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=1, existing=existing, workflow="agent")
    assert {r["tags"][0] for r in auto} == set(tx.coverage(existing, "agent")["missing"])
    with pytest.raises(ValueError):
        cs.generate(suite, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", tags=["nope"])


def test_cli_labels_cases_and_ladder(tmp_path, capsys):
    p = _reviews(tmp_path, [("c1", "*", "ana", 1, "pass", ""), ("c1", "*", "bo", 1, "fail", "")])
    assert cli(["labels", "agreement", str(p), "--out", str(tmp_path / "cons.csv"), "--fail-on-dispute"]) == 1
    assert "disputed: c1" in capsys.readouterr().out
    n = 40
    human = [i % 2 == 0 for i in range(n)]
    lab = tmp_path / "labels.csv"
    lab.write_text("case_id,target,label\n" + "".join(f"c{i},*,{'pass' if h else 'fail'}\n" for i, h in enumerate(human)))
    res = _result({"cheap": human, "strong": human}, n)
    (tmp_path / "r.json").write_text(json.dumps(res))
    assert cli(["calibrate", str(tmp_path / "r.json"), "--grader", "cheap", "--labels", str(lab), "--compare-to", "strong"]) == 0
    assert "approved" in capsys.readouterr().out
    assert cli(["cases", "tags"]) == 0 and "circular_handoff" in capsys.readouterr().out
    suite = tmp_path / "suite.yaml"
    write_jsonl(tmp_path / "d.jsonl", [{"id": "a", "input": "x", "tags": ["typical"]}])
    suite.write_text("name: s\ndataset: d.jsonl\npack: tool-agent@0.1.0\n")
    assert cli(["cases", "coverage", str(suite), "--strict"]) == 1
    assert re.search(r"missing: .*typos", capsys.readouterr().out)


def test_scores_without_length_details_have_no_length_bias():
    sc = Score(grader="g", case_id="c1", target="t", value=1.0, passed=True)
    assert "length_bias" not in summarise("t", [Case(id="c1", input="x")], [], [sc])["graders"]["g"]


def test_calibration_file_shapes_all_load(tmp_path):
    e = {"grader": "f", "n": 40, "kappa": 0.8, "kappa_ci": [0.6, 0.9]}
    for data in ({"graders": [e]}, [e], e, {"f": {k: v for k, v in e.items() if k != "grader"}}):
        p = tmp_path / "c.json"
        p.write_text(json.dumps(data))
        got = cal.load_calibration(p)
        assert list(got) == ["f"] and got["f"]["kappa"] == 0.8


# ---- representative vs stress, and calibration sampling --------------------------------------------------------------

def _sc(cid, v, g="g"):
    return Score(grader=g, case_id=cid, target="t", value=v, passed=v >= 0.5)


def test_representative_and_stress_splits_are_reported_and_weighted():
    cases = [Case(id="r1", input="x", tags=["typical"], weight=3), Case(id="r2", input="x", tags=["typical"], weight=1),
             Case(id="s1", input="x", tags=["edge"]), Case(id="s2", input="x", tags=["adversarial"]),
             Case(id="x1", input="x", tags=["edge"], split="representative")]
    scores = [_sc("r1", 1.0), _sc("r2", 0.0), _sc("s1", 0.0), _sc("s2", 0.0), _sc("x1", 1.0)]
    sp = summarise("t", cases, [], scores)["graders"]["g"]["splits"]
    rep, st = sp["representative"], sp["stress"]
    assert rep["n"] == 3 and st["n"] == 2 and rep["weighted"]
    assert rep["mean"] == pytest.approx((3 * 1 + 1 * 0 + 1 * 1) / 5) and rep["unweighted_mean"] == pytest.approx(2 / 3)
    assert st["mean"] == 0.0 and not st["weighted"]


def test_gates_on_splits_and_rule_validation():
    from genai_evals.config import GateSpec
    from genai_evals.gates import evaluate_gates, validate_rule
    cases = [Case(id=f"r{i}", input="x", tags=["typical"]) for i in range(5)] + [Case(id=f"s{i}", input="x", tags=["edge"]) for i in range(5)]
    scores = [_sc(f"r{i}", 1.0) for i in range(5)] + [_sc(f"s{i}", 0.0) for i in range(5)]
    summ = {"t": summarise("t", cases, [], scores)}
    gates = [GateSpec(name="rep", rule="mean(g, split='representative') >= 0.9"),
             GateSpec(name="stress", rule="mean(g, split='stress') >= 0.5"),
             GateSpec(name="mixed", rule="mean(g) >= 0.9")]
    res = {r.gate: r.status for r in evaluate_gates(gates, summ, [], min_n=5)}
    assert res == {"rep": "pass", "stress": "fail", "mixed": "fail"}
    assert validate_rule("mean(g, split='prod') >= 1", {"g"}) and validate_rule("p95(latency_ms, split='stress') < 1", {"g"})
    assert not validate_rule("ci_low(g, split='stress') >= 0.5", {"g"})


def test_doctor_flags_gates_that_mix_the_splits():
    suite = SuiteSpec(name="s", gates=[{"name": "headline", "rule": "mean(q) >= 0.9"},
                                       {"name": "rep", "rule": "mean(q, split='representative') >= 0.9"},
                                       {"name": "every-case", "rule": "mean(no_pii) >= 1.0"},
                                       {"name": "slo", "rule": "mean(latency_ms) <= 900"},
                                       {"name": "soft", "rule": "mean(q) >= 0.8", "severity": "warn"}])
    assert tx.mixed_gates(suite) == ["headline"]


def test_label_sheet_records_design_and_inverse_inclusion_weights():
    cases = [{"id": f"c{i}", "tags": ["rare" if i < 4 else "common"]} for i in range(40)]
    samples = [{"case_id": f"c{i}", "target": "t", "repeat": 0, "input": "q", "output": "a"} for i in range(40)]
    scores = [{"grader": "j", "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": i % 4 != 0} for i in range(40)]
    res = {"cases": cases, "samples": samples, "scores": scores}
    strat = lb.sample_items(res, "j", n=12)
    assert {i["sample_design"] for i in strat} == {"stratified"}
    # weights recover the population: sum of weights per stratum == stratum size
    assert sum(i["weight"] for i in strat) == pytest.approx(40)
    rare = [i for i in strat if i["tags"] == "rare"]
    assert rare and all(i["weight"] < 2 for i in rare)              # rare tag over-sampled -> small weights
    rnd = lb.sample_items(res, "j", n=10, design="random")
    assert {i["sample_design"] for i in rnd} == {"random"} and {i["weight"] for i in rnd} == {4.0}


def test_calibration_reports_weighted_slice_and_validation_agreement(tmp_path):
    # population: judge right on 'common', wrong half the time on 'rare'; humans pass 90% in production
    cases = [{"id": f"c{i}", "tags": ["rare" if i < 10 else "common"] + (["edge"] if i < 10 else ["typical"])} for i in range(100)]
    human = {f"c{i}": (i % 10 != 0) for i in range(100)}
    judge = {f"c{i}": (human[f"c{i}"] if i >= 10 or i % 2 else not human[f"c{i}"]) for i in range(100)}
    result = {"cases": cases, "samples": [], "stress_tags": ["edge", "adversarial"],
              "scores": [{"grader": "j", "case_id": c, "target": "t", "repeat": 0, "passed": judge[c]} for c in judge]}
    labels = {(c, "*"): h for c, h in human.items()}
    info = {(c, "*"): {"design": "stratified", "weight": 1.0 if int(c[1:]) >= 10 else 0.5} for c in human}
    out = cal.calibrate(result, "j", labels, sample_info=info, validation={(f"c{i}", "*"): human[f"c{i}"] for i in range(10, 60)})
    assert out["sample"] == {"design": "stratified", "weighted": True, "stratified_interval": False}
    assert out["weighted"]["accuracy"] > out["accuracy"]                       # rare, error-prone slice down-weighted
    assert out["slices"]["rare"]["accuracy"] == pytest.approx(0.5) and out["slices"]["common"]["accuracy"] == 1.0
    assert out["slices"]["split:stress"]["n"] == 10 and out["slices"]["split:representative"]["n"] == 90
    assert any("agrees noticeably less on" in w and "rare (50%, n=10)" in w for w in out["warnings"])
    assert any("not production estimates" in w for w in out["warnings"])
    assert out["validation"]["n"] == 50 and out["validation"]["accuracy"] == 1.0
    # a judge that clears the bar on the calibration sample but not on the validation sample is not trusted
    bad = dict(out, kappa=0.9, kappa_ci=[0.8, 0.95], n=100, validation={"n": 50, "kappa": 0.2, "kappa_ci": [0.0, 0.4]})
    assert "validation sample" in cal.untrusted_graders({"j": bad}, 0.6)["j"]


def test_read_sample_info_from_sheet(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("case_id,target,reviewer,label,sample_design,stratum,weight\nc1,t,ana,pass,stratified,rare|True,0.5\nc1,t,bo,pass,stratified,rare|True,0.5\n")
    assert cal.read_sample_info(p) == {("c1", "t"): {"design": "stratified", "stratum": "rare|True", "weight": 0.5}}


# ---- review round 3: units, provenance, stress minimums ------------------------------------------------------------

def _rep_result(n_cases=20, repeats=3):
    cases = [{"id": f"c{i}", "tags": ["a" if i % 2 else "b"]} for i in range(n_cases)]
    scores = [{"grader": "j", "case_id": f"c{i}", "target": "t", "repeat": r, "passed": (i + r) % 3 != 0}
              for i in range(n_cases) for r in range(repeats)]
    samples = [{"case_id": f"c{i}", "target": "t", "repeat": r, "input": f"q{i}", "output": f"a{i}.{r}"}
               for i in range(n_cases) for r in range(repeats)]
    return {"cases": cases, "scores": scores, "samples": samples}


def test_label_unit_is_case_and_target_and_repeats_never_fan_out(tmp_path):
    res = _rep_result()
    items = lb.sample_items(res, "j", n=100)
    assert len(items) == 20 and len({(i["case_id"], i["target"]) for i in items}) == 20       # one row per unit
    assert sum(i["weight"] for i in items) == pytest.approx(20)
    sheet = lb.write_sheet(items, tmp_path / "s.csv", ["ana"])
    rows = list(__import__("csv").DictReader(open(sheet)))
    for r in rows:                                        # label = the judge's verdict on the labelled repeat
        rep = int(r["repeat"])
        r["label"] = "pass" if (int(r["case_id"][1:]) + rep) % 3 != 0 else "fail"
    with open(sheet, "w", newline="") as f:
        w = __import__("csv").DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    out = cal.calibrate(res, "j", cal.read_labels(sheet), sample_info=cal.read_sample_info(sheet))
    assert out["n"] == 20 and out["accuracy"] == 1.0 and out["unit"] == "case, target"   # 20 units, not 60
    assert out["sample"]["stratified_interval"]
    # without the repeat column, repeat 0 is used and the guess is flagged
    plain = tmp_path / "p.csv"
    plain.write_text("case_id,target,label\n" + "".join(f"{r['case_id']},t,{r['label']}\n" for r in rows))
    out2 = cal.calibrate(res, "j", cal.read_labels(plain), sample_info=cal.read_sample_info(plain))
    assert out2["n"] == 20 and any("compared with repeat 0 only" in w for w in out2["warnings"])


def test_stratified_bootstrap_resamples_within_strata():
    import random
    strata = ["x"] * 3 + ["y"] * 7
    for _ in range(50):
        idx = cal._resample(10, strata, random.Random(_))
        assert sum(1 for i in idx if i < 3) == 3 and len(idx) == 10
    pairs = [(True, True)] * 5 + [(False, False)] * 4 + [(True, False)]
    assert cal._weighted_ci(pairs, [1.0] * 10, strata=["s"] * 5 + ["f"] * 5) is not None


def test_representative_set_needs_provenance_and_production_cases():
    from genai_evals.config import RepresentativeSample
    reps = [Case(id=f"r{i}", input="x", tags=["typical"], source="production", weight=2) for i in range(30)]
    cov = tx.coverage(reps, "rag", min_by_tier={"high": 0, "standard": 0}, min_production_share=0.8)
    assert any("no provenance" in p for p in cov["problems"])
    prov = RepresentativeSample(source="production", population="web chat", window="Oct 2026", method="random 1%")
    cov = tx.coverage(reps, "rag", provenance=prov, min_by_tier={"high": 0, "standard": 0})
    assert any("weights, but the provenance doesn't say" in p for p in cov["problems"])
    prov.weights = "intent share in October"
    syn = [Case(id=f"s{i}", input="x", tags=["typical"], source="synthetic") for i in range(30)]
    cov = tx.coverage(reps[:10] + syn, "rag", provenance=prov, min_by_tier={"high": 0, "standard": 0})
    assert any("source: production (need 80%)" in p for p in cov["problems"])
    assert "10/40 representative cases have source: production" in cov["evidence"]


def test_thin_stress_tags_and_gates_are_visible_before_a_run():
    cases = [Case(id=f"j{i}", input="x", tags=["adversarial", "jailbreak"]) for i in range(4)]
    cov = tx.coverage(cases, "agent")
    assert cov["thin"]["jailbreak"] == {"cases": 4, "needed": 10, "tier": "high"}
    suite = SuiteSpec(name="s", min_n=5, gates=[{"name": "jb", "rule": "mean(q, tag='jailbreak') >= 0.9"},
                                                {"name": "st", "rule": "mean(q, split='stress') >= 0.9", "severity": "info"},
                                                {"name": "ok", "rule": "mean(q, tag='jailbreak') >= 0.9", "min_n": 3}])
    thin = tx.thin_gates(suite, cases)
    assert [(t["gate"], t["severity"]) for t in thin] == [("jb", "block"), ("st", "info")]
    from genai_evals.packs import suite_coverage
    probs = suite_coverage(suite, cases)["problems"]
    assert any("will be invalid for lack of cases: jb (tag 'jailbreak': 4 < min_n 5)" in p for p in probs)
    assert not any("st (" in p for p in probs)                          # diagnostic gates are notes, not problems


def test_info_gates_never_fail_a_run():
    from genai_evals.config import GateSpec
    from genai_evals.gates import blocking_failures, evaluate_gates
    cases = [Case(id=f"c{i}", input="x", tags=["edge"]) for i in range(3)]
    summ = {"t": summarise("t", cases, [], [_sc(f"c{i}", 0.0) for i in range(3)])}
    res = evaluate_gates([GateSpec(name="d", rule="mean(g, split='stress') >= 0.9", severity="info")], summ, [], min_n=5)
    assert res[0].status == "invalid" and not blocking_failures(res)


def test_cases_sample_draws_units_not_failures_with_provenance():
    res = _rep_result(40, 2)
    res["run_id"] = "run_x"
    res["sources"] = [{"source": "agentcore", "window": {"last": "7d"}, "sampling": 0.1, "sampling_method": "hash"}]
    rows, prov = cs.sample_representative(res, n=10, by_tag=True, population="claims chat")
    assert len(rows) == 10 and len({r["metadata"]["original_case_id"] for r in rows}) == 10
    assert all(r["split"] == "representative" and r["source"] == "production" and r["review_status"] == "draft" for r in rows)
    assert sum(r["weight"] for r in rows) == pytest.approx(400)          # 40 graded units / 0.1 online sampling
    assert "captured telemetry sample only" in prov["represents"] and "agentcore" in prov["represents"]
    assert prov["source"] == "production" and prov["window"] == "agentcore: last 7d" and prov["weights"]
    assert "stratified" in prov["method"] and prov["telemetry_sampling"][0]["sampling"] == 0.1
    res["sources"][0]["fixture"] = True
    rows, prov = cs.sample_representative(res, n=5)
    assert rows[0]["source"] == "synthetic" and prov["source"].startswith("fixture") and "weight" not in rows[0]


def test_cases_sample_weights_by_capture_rate_and_online_sampling():
    res = _rep_result(40, 1)
    res["run_id"] = "run_y"
    for smp in res["samples"]:                       # c0-c19 from web (all traffic captured), c20-c39 from app (10%)
        smp["origin"] = "source:web" if int(smp["case_id"][1:]) < 20 else "source:app"
    res["sources"] = [{"source": "web", "capture_rate": 1.0, "sampling": 0.5},
                      {"source": "app", "capture_rate": 0.1, "sampling": 1.0}]
    rows, prov = cs.sample_representative(res, n=40)
    w = {r["metadata"]["telemetry_source"]: r["weight"] for r in rows}
    assert w == {"web": pytest.approx(2.0), "app": pytest.approx(10.0)}   # 1/(1*0.5*1), 1/(0.1*1*1)
    assert sum(r["weight"] for r in rows) == pytest.approx(240)           # 40 web + 200 app sessions in production
    assert prov["represents"] == "production traffic" and "capture rate" in prov["weights"]
    app = next(r for r in rows if r["metadata"]["telemetry_source"] == "app")
    assert app["metadata"]["inclusion"] == {"capture_rate": 0.1, "online_sampling": 1.0, "selection": 1.0}


def test_cases_sample_equal_known_rates_need_no_weights_and_unknown_rates_are_flagged():
    res = _rep_result(20, 1)
    res["sources"] = [{"source": "web", "capture_rate": 0.2, "sampling": 0.5}]
    rows, prov = cs.sample_representative(res, n=10)
    assert "weight" not in rows[0] and prov["represents"] == "production traffic"
    res["sources"] = [{"source": "web", "sampling": 0.5}]
    rows, prov = cs.sample_representative(res, n=10)
    assert prov["represents"].startswith("the captured telemetry sample only") and "web" in prov["represents"]
    res.pop("sources")
    rows, prov = cs.sample_representative(res, n=10)
    assert "no telemetry source recorded" in prov["represents"]


def _ladder_fixture():
    """200 labelled units in two strata: 'hard' (over-sampled, weight 1) and 'common' (weight 20 in production).
    The strong judge errs on 5 hard units, the cheap judge on 5 common ones: equal on the sample, not in production."""
    n = 200
    human = [i % 3 != 0 for i in range(n)]
    strat = ["hard" if i < 100 else "common" for i in range(n)]
    strong = [not h if i in (3, 23, 43, 63, 83) else h for i, h in enumerate(human)]
    cheap = [not h if i in (103, 123, 143, 163, 183) else h for i, h in enumerate(human)]
    labels = {(f"c{i}", "t"): h for i, h in enumerate(human)}
    info = {(f"c{i}", "t"): {"repeat": 0, "design": "stratified", "stratum": strat[i],
                             "weight": 1.0 if strat[i] == "hard" else 20.0} for i in range(n)}
    res = _result({"cheap": cheap, "strong": strong}, n)
    return res, labels, info, human


def test_compare_judges_uses_the_sampling_design():
    res, labels, info, _ = _ladder_fixture()
    plain = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1)
    assert plain["approved"] and plain["kappa_diff"] == pytest.approx(0)             # looks equal on the sample
    aware = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1, info=info)
    assert aware["basis"].startswith("calibration sample, production-weighted")
    assert aware["calibration_sample"]["stratified_interval"] and aware["calibration_sample"]["weighted"]
    assert aware["kappa_diff"] < -0.05 and not aware["approved"] and "may be worse" in aware["reason"]
    unweighted = {k: {kk: vv for kk, vv in v.items() if kk != "weight"} for k, v in info.items()}
    uw = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1, info=unweighted)
    assert not uw["approved"] and "--validation-labels" in uw["reason"]


def test_compare_judges_approves_on_the_random_validation_sample():
    res, labels, info, human = _ladder_fixture()
    # a random sample of production: mostly 'common' units, where the cheap judge errs
    val = {(f"c{i}", "t"): human[i] for i in range(100, 200)}
    vinfo = {k: {"repeat": 0, "design": "random"} for k in val}
    out = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1, info=info,
                             validation=val, validation_info=vinfo)
    assert out["basis"] == "validation sample" and out["n"] == 100 and out["validation_sample"]["design"] == "random"
    assert not out["approved"] and "may be worse" in out["reason"]
    # validation where both judges are equally good: approved on it, calibration sample still reported
    val2 = {(f"c{i}", "t"): human[i] for i in list(range(0, 3)) + list(range(4, 103)) if i not in (23, 43, 63, 83)}
    out2 = cal.compare_judges((res, "cheap"), (res, "strong"), val2 | {}, margin=0.1, validation=val2,
                              validation_info={k: {"repeat": 0, "design": "random"} for k in val2})
    assert out2["basis"] == "validation sample" and out2["approved"] and out2["calibration_sample"]["n"] == out2["n"]


def _ladder_with_failures(cheap_fail=(), strong_fail=(), target_fail=(), n=200):
    """Both judges agree with humans except: the strong judge errs on 4 units; `*_fail` units have no verdict
    (judge error), `target_fail` units have a failed system output (nothing to judge)."""
    human = [i % 3 != 0 for i in range(n)]
    scores, samples = [], []
    for i, h in enumerate(human):
        samples.append({"case_id": f"c{i}", "target": "t", "repeat": 0, "error": "timeout" if i in target_fail else None})
        for g, fails in (("cheap", cheap_fail), ("strong", strong_fail)):
            if i in target_fail:
                scores.append({"grader": g, "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": None,
                               "error": "target error: timeout"})
            elif i in fails:
                scores.append({"grader": g, "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": None,
                               "error": "RateLimitError: judge"})
            else:
                v = (not h) if (g == "strong" and i in (5, 55, 105, 155)) else h
                scores.append({"grader": g, "case_id": f"c{i}", "target": "t", "repeat": 0, "passed": v, "value": float(v)})
    labels = {(f"c{i}", "t"): h for i, h in enumerate(human)}
    return {"scores": scores, "samples": samples}, labels


def test_judge_ladder_reports_coverage_and_rejects_a_judge_that_fails_on_units():
    hard = tuple(range(0, 60, 3))                         # the cheap judge errors on 20 units (10%)
    res, labels = _ladder_with_failures(cheap_fail=hard)
    for sc in res["scores"]:                              # persistent errors: retrying won't help
        if sc.get("error", "").startswith("RateLimitError"):
            sc["error"] = "unparseable verdict: I think the answer is fine"
    out = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1)
    cov = out["coverage"]["cheap"]
    assert (cov["gradable"], cov["scored"], cov["judge_error"]) == (200, 180, 20) and cov["failure_rate"] == pytest.approx(0.1)
    assert out["coverage"]["strong"]["failure_rate"] == 0
    assert out["kappa_diff_ci"][0] > -0.1                 # on the overlap alone it looks at least as good
    assert not out["approved"] and "failed to grade 10%" in out["reason"]
    assert out["decision"] == "not_approved" and cov["persistent_error"] == 20 and cov["transient_error"] == 0
    # even with a lax failure limit, counting the ungraded units as disagreements shows it is worse
    lax = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1, max_judge_failure=0.5)
    assert not lax["approved"] and "ungraded units count as disagreements" in lax["reason"]
    assert lax["kappa_diff_ci_imputed"][0] < -0.1


def test_judge_ladder_target_errors_are_not_judge_failures():
    res, labels = _ladder_with_failures(target_fail=tuple(range(1, 40, 4)))       # 10 failed system outputs
    out = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1)
    for role in ("cheap", "strong"):
        c = out["coverage"][role]
        assert c["target_error"] == 10 and c["gradable"] == 190 and c["failure_rate"] == 0
    assert out["approved"] and out["n"] == 190


def test_calibrate_reports_judge_coverage():
    res, labels = _ladder_with_failures(cheap_fail=tuple(range(0, 60, 3)))
    out = cal.calibrate(res, "cheap", labels)
    assert out["coverage"]["judge_error"] == 20 and out["n"] == 180
    assert any("failed to grade 10% of labelled units" in w for w in out["warnings"])


def test_error_classification():
    from genai_evals.retry import classify_error_text as k
    assert k("RateLimitError: Error code: 429") == "transient"
    assert k("APITimeoutError: Request timed out.") == "transient"
    assert k("InternalServerError: 503 Service Unavailable") == "transient"
    assert k("ThrottlingException: Rate exceeded") == "transient"
    assert k("unparseable verdict: The answer looks fine") == "persistent"
    assert k("AuthenticationError: invalid api key") == "persistent"
    assert k("ValueError: timeout must be positive") == "persistent"           # a ValueError is not a timeout
    assert k("RateLimitError: 429; unparseable verdict: x") == "persistent"    # one judge of a panel can't be fixed
    assert k("RateLimitError: 429; APITimeoutError: timed out") == "transient"
    assert k("target error: boom") == "target" and k(None) is None


def test_judge_ladder_says_retry_first_when_only_transient_errors_block_approval():
    res, labels = _ladder_with_failures(cheap_fail=tuple(range(0, 60, 3)))   # 20 RateLimitErrors
    out = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1)
    cov = out["coverage"]["cheap"]
    assert cov["transient_error"] == 20 and cov["persistent_error"] == 0 and cov["failure_rate_after_retry_best_case"] == 0
    assert not out["approved"] and out["decision"] == "retry_first"
    assert "genai-evals regrade" in out["retry"] and "--grader cheap" in out["retry"]
    # a cheaper judge that is worse on the units it did grade can't be rescued by retries
    for sc in res["scores"]:
        if sc["grader"] == "cheap" and sc.get("passed") is not None and int(sc["case_id"][1:]) % 7 == 1:
            sc["passed"] = not sc["passed"]
    worse = cal.compare_judges((res, "cheap"), (res, "strong"), labels, margin=0.1)
    assert worse["decision"] == "not_approved" and "can't change the decision" in worse["retry"]


def test_calibrate_points_to_regrade_for_transient_errors():
    res, labels = _ladder_with_failures(cheap_fail=(0, 3))
    out = cal.calibrate(res, "cheap", labels)
    assert out["coverage"]["transient_error"] == 2
    assert any("genai-evals regrade" in w for w in out["warnings"])


def _regrade_suite(tmp_path):
    from genai_evals.config import ProvidersConfig
    data = write_jsonl(tmp_path / "d.jsonl", [{"id": f"c{i}", "input": f"question {i}", "reference": {"answer": f"question {i}"}}
                                              for i in range(6)])
    suite = SuiteSpec(name="rg", dataset=str(data), judges=["judge-a"], tokenize=False,
                      targets=[{"name": "echo", "kind": "model", "profile": "echo", "system": "Repeat: {{ input }}"}],
                      graders=[{"name": "q", "type": "rubric", "rubric": "Repeats the question."}],
                      gates=[{"name": "quality", "rule": "mean(q) >= 0.0"}], base_dir=str(tmp_path))
    return suite, ProvidersConfig(**MOCK_PROVIDERS)


def test_regrade_retries_transient_judge_failures_and_recomputes(tmp_path, monkeypatch):
    from genai_evals.runner import Runner
    suite, prov = _regrade_suite(tmp_path)
    first = Runner(suite, prov, use_cache=False).run()
    good = {sc["case_id"]: sc["value"] for sc in first["scores"] if sc["grader"] == "q"}
    for sc in first["scores"]:
        if sc["case_id"] in ("c0", "c1"):
            sc.update(value=None, passed=None, error="RateLimitError: Error code: 429", error_kind="transient")
        if sc["case_id"] == "c2":
            sc.update(value=None, passed=None, error="unparseable verdict: hmm", error_kind="persistent")
    # the judge still rate-limits once before answering
    from genai_evals.graders import judges
    real, calls = judges.Rubric.grade, {}

    async def flaky(self, sample, case):
        calls[sample.case_id] = calls.get(sample.case_id, 0) + 1
        if calls[sample.case_id] == 1:
            raise RuntimeError("RateLimitError: 429 Too Many Requests")
        return await real(self, sample, case)
    monkeypatch.setattr(judges.Rubric, "grade", flaky)
    new = Runner(suite, prov, use_cache=False).regrade(first, attempts=3, backoff_s=0)
    r = new["regrade"]
    assert (r["units"], r["recovered"], r["persistent"], r["still_transient"]) == (2, 2, 0, 0)
    assert r["attempts"] == 4 and set(calls) == {"c0", "c1"}                  # c2 (persistent) not retried by default
    after = {sc["case_id"]: sc for sc in new["scores"] if sc["grader"] == "q"}
    assert after["c0"]["value"] == good["c0"] and after["c0"]["attempts"] == 3 and after["c0"]["regraded"]
    assert after["c2"]["error"].startswith("unparseable")
    assert new["regraded_from"] == first["run_id"] and new["run_id"] != first["run_id"]
    n_scored = new["summary"]["echo"]["graders"]["q"]
    assert n_scored  # summary recomputed from the merged scores
    # --only errors also retries the persistent one
    again = Runner(suite, prov, use_cache=False).regrade(new, only="errors", attempts=1, backoff_s=0)
    assert again["regrade"]["units"] == 1


def test_regrade_refuses_a_changed_grader(tmp_path):
    from genai_evals.runner import Runner, SuiteError
    suite, prov = _regrade_suite(tmp_path)
    first = Runner(suite, prov, use_cache=False).run()
    suite.graders[0].rubric = "Something else entirely."
    with pytest.raises(SuiteError, match="config changed"):
        Runner(suite, prov, use_cache=False).regrade(first)
