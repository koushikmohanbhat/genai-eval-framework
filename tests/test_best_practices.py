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
    assert not few["approved"] and "shared labelled items" in few["reason"]


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
    assert cov["untagged"] == 1 and cov["hard_share"] == 0.5 and "typos" in cov["present"]
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
