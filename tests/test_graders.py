import asyncio

import pytest

from genai_evals.config import GraderSpec, ProvidersConfig, SuiteSpec
from genai_evals.graders import GradeContext, build_grader
from genai_evals.models import Case, ContextChunk, Sample, ToolCall
from genai_evals.providers import ProviderPool

from .conftest import MOCK_PROVIDERS


def ctx(judges=("judge-a", "judge-b"), **policy):
    prov = ProvidersConfig(**MOCK_PROVIDERS)
    return GradeContext(suite=SuiteSpec(name="t", judges=list(judges), judge_policy=policy, embeddings="emb"), pool=ProviderPool(prov), providers=prov)


def grade(spec: dict, sample: Sample, case: Case, c=None):
    g = build_grader(GraderSpec(**spec), c or ctx())
    return asyncio.run(g.grade(sample, case))


S = dict(case_id="c1", target="t")


def test_field_match_normalisers_and_lists():
    case = Case(id="c1", input="x", reference={"fields": {"date": "2026-03-12", "amount": 120000, "name": "Rahul Verma", "reg": "KA 05 MN 4521", "tags": ["a", "b"]}})
    s = Sample(**S, output_json={"date": "12 Mar 2026", "amount": "₹1.2 lakh", "name": "verma rahul", "reg": "KA05MN4521", "tags": ["b"]})
    sc = grade({"name": "f", "type": "field_match", "fields": {"date": "date", "amount": {"normaliser": "amount", "weight": 2}, "name": "name",
                                                              "reg": "upper_alnum", "tags": "text"}}, s, case)
    assert sc.details["per_field"]["date"]["score"] == 1 and sc.details["per_field"]["amount"]["score"] == 1 and sc.details["per_field"]["name"]["score"] == 1
    assert sc.details["per_field"]["tags"]["score"] == pytest.approx(2 / 3)
    assert not sc.passed and sc.value == pytest.approx((1 + 2 + 1 + 1 + 2 / 3) / 6)


def test_string_graders():
    case = Case(id="c1", input="x", reference={"answer": "Within 48 hours"})
    s = Sample(**S, output="  within   48 HOURS ")
    assert grade({"name": "e", "type": "exact"}, s, case).passed
    assert grade({"name": "c", "type": "contains", "value": ["48", "hours"], "mode": "all"}, s, case).passed
    assert not grade({"name": "n", "type": "contains", "value": "hours", "negate": True}, s, case).passed
    assert grade({"name": "r", "type": "regex", "pattern": r"\d+\s+hours"}, s, case).passed
    assert grade({"name": "rl", "type": "rouge"}, s, case).value > 0.9
    js = grade({"name": "j", "type": "json_schema", "schema": {"type": "object", "required": ["a"]}}, Sample(**S, output='{"b": 1}'), case)
    assert not js.passed and js.details["errors"]


def test_tool_graders():
    case = Case(id="c1", input="x", reference={"tool_calls": [{"name": "lookup_policy", "args": {"policy_number": "mot-1"}}],
                                               "tool_sequence": ["lookup_policy", "create_claim"], "final_state": {"claims_created": 1}})
    traj = [ToolCall(name="lookup_policy", args={"policy_number": "MOT-1"}), ToolCall(name="check_coverage", args={}),
            ToolCall(name="create_claim", args={"amount": "10,000"}), ToolCall(name="to_human", kind="handoff", args={"to": "specialist"})]
    s = Sample(**S, trajectory=traj, final_state={"claims_created": 1, "x": 2}, steps=4)
    assert grade({"name": "a", "type": "tool_args_match"}, s, case).passed
    assert grade({"name": "q", "type": "tool_sequence"}, s, case).passed
    assert not grade({"name": "q2", "type": "tool_sequence", "mode": "exact"}, s, case).passed
    assert grade({"name": "fs", "type": "final_state"}, s, case).passed
    assert grade({"name": "tc", "type": "tool_called", "tool": "create_claim", "args": {"amount": 10000}}, s, case).passed
    assert not grade({"name": "tn", "type": "tool_not_called", "tool": "create_claim"}, s, case).passed
    assert grade({"name": "h", "type": "handoff_to", "agent": "specialist"}, s, case).passed
    assert not grade({"name": "sb", "type": "step_budget", "max_steps": 3}, s, case).passed


def test_retrieval_citation_refusal():
    case = Case(id="c1", input="q", reference={"gold_passages": ["d2", "d9"], "answerable": True})
    s = Sample(**S, output="Do it in 48 hours [d2]. Also [d7].", context=[ContextChunk(id="d1"), ContextChunk(id="d2"), ContextChunk(id="d3")])
    assert grade({"name": "r", "type": "retrieval_recall_at_k", "k": 3}, s, case).value == 0.5
    assert grade({"name": "m", "type": "mrr"}, s, case).value == 0.5
    assert 0 < grade({"name": "n", "type": "ndcg", "k": 3}, s, case).value < 1
    cit = grade({"name": "c", "type": "citation_accuracy"}, s, case)
    assert cit.value == 0.5 and cit.details["invalid"] == ["d7"]
    unans = Case(id="c1", input="q", reference={"answerable": False})
    assert grade({"name": "x", "type": "correct_refusal"}, Sample(**S, output="I can't find this in your policy documents."), unans).passed
    assert not grade({"name": "x", "type": "correct_refusal"}, Sample(**S, output="Yes it is covered."), unans).passed


def test_pii_leak_detects_valid_identifiers_only():
    case = Case(id="c1", input="my number is 98765 43210")
    leak = grade({"name": "p", "type": "pii_leak"}, Sample(**S, output="Aadhaar 2345 6789 0124 and PAN ABCPE1234F"), case)
    assert not leak.passed and set(leak.details["leaked_types"]) == {"AADHAAR", "PAN"}
    assert grade({"name": "p", "type": "pii_leak"}, Sample(**S, output="ref 2345 6789 0125"), case).passed   # fails Verhoeff
    assert grade({"name": "p", "type": "pii_leak", "allow_echo": True}, Sample(**S, output="we'll call 98765 43210"), case).passed


def test_ops_and_guardrail():
    case = Case(id="c1", input="x", reference={"guardrail": True})
    s = Sample(**S, latency_ms=900, cost_usd=0.02, metadata={"guardrail": {"action": "INTERVENED"}})
    assert grade({"name": "l", "type": "latency", "max_ms": 1000}, s, case).passed
    assert not grade({"name": "c", "type": "cost", "max_usd": 0.01}, s, case).passed
    assert grade({"name": "g", "type": "guardrail_triggered"}, s, case).passed
    assert grade({"name": "c", "type": "cost", "max_usd": 0.01}, Sample(**S), case).skipped


def test_judge_panel_records_each_judge_and_disagreement():
    case = Case(id="c1", input="How soon must I report?", reference={"answer": "within 48 hours by calling the claims desk"})
    s = Sample(**S, output="Report within 48 hours.")
    sc = grade({"name": "rm", "type": "rubric", "use_reference": True}, s, case)
    assert sc.details["n_judges"] == 2 and {j["judge"] for j in sc.details["judges"]} == {"judge-a", "judge-b"}
    assert sc.details["disagreement"] > 0


def test_faithfulness_and_policy_with_mock_judges():
    case = Case(id="c1", input="q")
    s = Sample(**S, output="You must inform us within 48 hours. Claims are settled within 7 days.",
               context=[ContextChunk(id="pw-03", text="You must inform us of any accident within 48 hours.")])
    f = grade({"name": "f", "type": "faithfulness"}, s, case)
    assert f.value == pytest.approx(0.5) and not f.passed and "settled within 7 days" in " ".join(f.details["unsupported_claims"])
    assert grade({"name": "f", "type": "faithfulness"}, Sample(**S, output="x"), case).skipped
    pol = 'Never promise payment. Banned: "will be paid".'
    assert not grade({"name": "p", "type": "policy", "policy": pol}, Sample(**S, output="Your claim will be paid."), case).passed
    assert grade({"name": "p", "type": "policy", "policy": pol}, Sample(**S, output="A surveyor will call."), case).passed


def test_judge_family_policy_and_missing_judges():
    c = ctx(require_different_family=True)
    c.target_families = {"t": "openai"}
    sc = grade({"name": "r", "type": "rubric"}, Sample(**S, output="hello there friend"), Case(id="c1", input="q"), c)
    assert [j["judge"] for j in sc.details["judges"]] == ["judge-b"]
    c2 = ctx(judges=())
    with pytest.raises(ValueError, match="no default judge"):
        grade({"name": "r", "type": "rubric"}, Sample(**S, output="x"), Case(id="c1", input="q"), c2)


def test_pairwise_position_swap():
    c = ctx()
    case = Case(id="c1", input="q", reference={"answer": "inform within 48 hours with documents"})
    a = Sample(case_id="c1", target="a", output="Inform within 48 hours with documents.")
    b = Sample(case_id="c1", target="b", output="Call us sometime.")
    c.samples = {("a", "c1", 0): a, ("b", "c1", 0): b}
    sc = grade({"name": "pw", "type": "pairwise", "against": "b", "use_reference": True}, a, case, c)
    assert sc.value == 1.0 and sc.details["position_consistent"]


def test_embedding_similarity_uses_profile():
    case = Case(id="c1", input="q", reference={"answer": "inform us within 48 hours"})
    assert grade({"name": "e", "type": "embedding_similarity"}, Sample(**S, output="inform us within 48 hours"), case).value == pytest.approx(1.0)
