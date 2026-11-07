import pytest

from genai_evals.config import GateSpec
from genai_evals.gates import evaluate_gates, validate_rule
from genai_evals.models import Case, Sample, Score
from genai_evals.stats import bootstrap_ci, mcnemar_p, paired_delta, summarise


def _scores(vals, grader="g", target="t", repeats=1):
    out = []
    for i, v in enumerate(vals):
        for r in range(repeats):
            vv = v[r] if isinstance(v, list) else v
            out.append(Score(grader=grader, case_id=f"c{i}", target=target, repeat=r, value=vv, passed=vv >= 0.5))
    return out


def test_bootstrap_ci_contains_mean_and_narrows():
    small = bootstrap_ci([0, 1] * 5)
    big = bootstrap_ci([0, 1] * 200)
    assert small[0] < 0.5 < small[1] and (big[1] - big[0]) < (small[1] - small[0])


def test_mcnemar_and_paired_delta():
    assert mcnemar_p(0, 0) == 1.0 and mcnemar_p(10, 0) < 0.01
    cur = {f"c{i}": 1.0 for i in range(40)}
    base = {f"c{i}": (0.0 if i < 20 else 1.0) for i in range(40)}
    d = paired_delta(cur, base)
    assert d["delta"] == 0.5 and d["significant"] and len(d["improved"]) == 20 and "warning" not in d
    d2 = paired_delta({"a": 1, "b": 0}, {"a": 0, "b": 1})
    assert not d2["significant"] and "warning" in d2


def test_summary_pass_k_and_slices_and_critical():
    cases = [Case(id=f"c{i}", input="x", tags=["hi"] if i < 2 else ["en"], critical=(i == 3)) for i in range(4)]
    scores = _scores([[1, 1, 1], [1, 0, 1], [0, 0, 0], [1, 1, 0]], repeats=3)
    samples = [Sample(case_id=f"c{i}", target="t", repeat=r, latency_ms=100 * (i + 1)) for i in range(4) for r in range(3)]
    s = summarise("t", cases, samples, scores)
    g = s["graders"]["g"]
    assert g["pass_at_k"] == 0.75 and g["pass_hat_k"] == 0.25
    assert s["slices"]["hi"]["g"] == pytest.approx((1 + 2 / 3) / 2)
    assert s["critical"]["failed"] == ["c3"] and s["ops"]["latency_ms"]["p50"] == 250


def _summ(vals, critical=()):
    cases = [Case(id=f"c{i}", input="x", tags=["x"], critical=(f"c{i}" in critical)) for i in range(len(vals))]
    samples = [Sample(case_id=f"c{i}", target="t", latency_ms=1000 + i, cost_usd=0.01) for i in range(len(vals))]
    return summarise("t", cases, samples, _scores(vals)), samples


def test_gate_dsl_evaluation():
    s, samples = _summ([1, 1, 1, 0])
    base, _ = _summ([1, 0, 0, 0])
    gates = [GateSpec(name="a", rule="mean(g) >= 0.7 and ci_low(g) >= 0"), GateSpec(name="b", rule="p95(latency_ms) <= 1002"),
             GateSpec(name="c", rule="delta(g) > 0"), GateSpec(name="d", rule="mean(g, tag='x') >= 0.9", severity="warn"),
             GateSpec(name="e", rule="mean(cost_usd) <= 0.01 and critical_pass()"), GateSpec(name="f", rule="not significant(g) or delta(g) > -0.1")]
    res = {r.gate: r for r in evaluate_gates(gates, {"t": s}, samples, {"t": base})}
    assert res["a"].passed and not res["b"].passed and res["c"].passed and not res["d"].passed and res["e"].passed and res["f"].passed
    assert res["c"].values["delta(g)"] == 0.5


def test_gate_without_baseline_is_invalid_unless_allowed_and_untrusted_judge():
    s, samples = _summ([1, 1])
    r = evaluate_gates([GateSpec(name="x", rule="delta(g) > -0.01")], {"t": s}, samples, None)[0]
    assert r.status == "invalid" and not r.passed and "no baseline" in r.message
    r = evaluate_gates([GateSpec(name="x", rule="delta(g) > -0.01", allow_missing_baseline=True)], {"t": s}, samples, None)[0]
    assert r.passed and r.values["delta(g)"] == "no baseline"
    r2 = evaluate_gates([GateSpec(name="y", rule="mean(g) >= 0.5")], {"t": s}, samples, None, untrusted={"g": "kappa 0.1 < 0.4"})[0]
    assert r2.status == "invalid" and not r2.passed


@pytest.mark.parametrize("rule", ["__import__('os').system('x')", "mean(g).real", "[x for x in g]", "lambda: 1", "open('f')"])
def test_gate_rules_are_sandboxed(rule):
    probs = validate_rule(rule, {"g"})
    assert probs


def test_validate_rule_names():
    assert validate_rule("mean(g) >= 1", {"g"}) == []
    assert any("unknown grader" in p for p in validate_rule("mean(nope) >= 1", {"g"}))
    assert any("unknown function" in p for p in validate_rule("median(g) >= 1", {"g"}))


def test_errors_count_as_failures_and_min_n():
    from genai_evals.models import Case, Sample, Score
    from genai_evals.stats import summarise

    cases = [Case(id=f"c{i}", input="x", tags=["a"] if i < 2 else []) for i in range(6)]
    samples = [Sample(case_id=c.id, target="t") for c in cases]
    scores = [Score(grader="g", case_id=c.id, target="t", value=1.0, passed=True) for c in cases[:4]]
    scores += [Score(grader="g", case_id=c.id, target="t", error="Timeout") for c in cases[4:]]
    fail = summarise("t", cases, samples, scores, on_error="fail")
    excl = summarise("t", cases, samples, scores, on_error="exclude")
    assert fail["graders"]["g"]["mean"] == 4 / 6 and fail["graders"]["g"]["n"] == 6
    assert excl["graders"]["g"]["mean"] == 1.0 and excl["graders"]["g"]["errors"] == 2
    # a 2-case slice cannot decide a gate under min_n 5; the overall metric (6 cases) can
    r = evaluate_gates([GateSpec(name="s", rule="mean(g, tag='a') >= 0.5")], {"t": fail}, samples, None, min_n=5)[0]
    assert r.status == "invalid" and "slice 'a'" in r.message
    r = evaluate_gates([GateSpec(name="o", rule="mean(g) >= 0.5")], {"t": fail}, samples, None, min_n=5)[0]
    assert r.passed
    r = evaluate_gates([GateSpec(name="o", rule="mean(g) >= 0.5", min_n=10)], {"t": fail}, samples, None, min_n=5)[0]
    assert r.status == "invalid"
