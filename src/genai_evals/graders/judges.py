"""LLM-as-judge graders, always run through an explicitly configured judge panel (one or more provider profiles).

Hygiene enforced here: temperature 0, structured JSON verdicts, optional "judge must be a different model family
than the target", position swap for pairwise, per-judge results and disagreement recorded on every score.
"""
from __future__ import annotations

import asyncio
import statistics
from typing import Any

from ..models import Case, ContextChunk, Message, Sample, Score
from ..targets.base import parse_json
from .base import Grader, dig, register

RUBRIC_PROMPT = """<task>{task}</task>
You are a strict, impartial evaluator. Grade the OUTPUT against the RUBRIC{ref_hint}.
<scale>{scale}</scale>
<rubric>
{rubric}
</rubric>
<question>
{question}
</question>
{reference_block}<output>
{output}
</output>
Respond with JSON only: {schema}"""

FAITH_PROMPT = """<task>faithfulness</task>
Split the OUTPUT into atomic factual claims. For each claim decide whether the CONTEXT supports it.
verdict: "supported" (stated or directly implied by the context), "contradicted" (context says otherwise),
"unsupported" (not in the context). Ignore greetings and statements about what the assistant will do.
<context>
{context}
</context>
<output>
{output}
</output>
Respond with JSON only: {{"claims": [{{"claim": "...", "verdict": "supported|contradicted|unsupported", "evidence": "..."}}]}}"""

RELEVANCE_PROMPT = """<task>answer_relevance</task>
Rate how directly and completely the OUTPUT answers the QUESTION (1 = irrelevant, 5 = fully answers it). Do not judge correctness.
<question>
{question}
</question>
<output>
{output}
</output>
Respond with JSON only: {{"score": <1-5>, "reasoning": "..."}}"""

PAIRWISE_PROMPT = """<task>pairwise</task>
Compare two responses to the same request using the CRITERIA. Pick the better one, or "tie".
<criteria>
{criteria}
</criteria>
<question>
{question}
</question>
{reference_block}<output_a>
{a}
</output_a>
<output_b>
{b}
</output_b>
Respond with JSON only: {{"winner": "A|B|tie", "reasoning": "..."}}"""

REFUSAL_PROMPT = """<task>refusal</task>
Did the OUTPUT decline to answer or say the information is not available (rather than answering)?
<output>
{output}
</output>
Respond with JSON only: {{"refused": true|false, "reasoning": "..."}}"""


class JudgePanel:
    def __init__(self, grader: Grader, sample: Sample):
        ctx = grader.ctx
        names = grader.spec.judges or ctx.suite.judges
        if not names:
            raise ValueError(f"grader '{grader.name}' needs judges: set `judges:` on the suite or the grader (no default judge)")
        policy = ctx.suite.judge_policy
        fam = ctx.target_families.get(sample.target)
        if policy.get("require_different_family") and fam:
            kept = [n for n in names if ctx.providers.get(n).model_family != fam]
            if not kept:
                raise ValueError(f"no judge from a different model family than target '{sample.target}' ({fam})")
            names = kept
        self.names = names
        self.ctx = ctx
        self.aggregate = grader.p.get("aggregate") or policy.get("aggregate", "mean")

    async def ask(self, prompt: str) -> list[dict[str, Any]]:
        async def one(name: str) -> dict[str, Any]:
            prov = self.ctx.pool.get(name)
            try:
                r = await prov.complete([Message(role="user", content=prompt)], temperature=0.0, json_mode=True)
                data = parse_json(r.text)
                if not isinstance(data, dict):
                    return {"judge": name, "error": f"unparseable verdict: {r.text[:200]}"}
                return {"judge": name, "data": data, "cost": prov.cost(r.usage) or 0.0}
            except Exception as e:  # noqa: BLE001
                return {"judge": name, "error": f"{type(e).__name__}: {e}"}
        return await asyncio.gather(*[one(n) for n in self.names])


def _combine(grader: Grader, sample: Sample, results: list[dict], to_value, reasoning_key: str = "reasoning", extra=None) -> Score:
    ok = [r for r in results if "data" in r]
    per = []
    for r in results:
        if "data" in r:
            v = to_value(r["data"])
            per.append({"judge": r["judge"], "value": v, "reasoning": r["data"].get(reasoning_key)})
        else:
            per.append({"judge": r["judge"], "error": r["error"]})
    cost = sum(r.get("cost", 0.0) for r in results)
    vals = [p["value"] for p in per if p.get("value") is not None]
    if not vals:
        return Score(grader=grader.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                     error="; ".join(p.get("error", "") for p in per), details={"judges": per}, cost_usd=cost)
    thr = grader.spec.threshold if grader.spec.threshold is not None else grader.default_threshold()
    agg = grader.p.get("aggregate") or grader.ctx.suite.judge_policy.get("aggregate", "mean")
    if agg == "min":
        value = min(vals)
    elif agg == "majority":
        value = sum(v >= thr for v in vals) / len(vals)
    else:
        value = statistics.fmean(vals)
    passed = (sum(v >= thr for v in vals) > len(vals) / 2) if agg == "majority" else value >= thr
    details = {"judges": per, "disagreement": (max(vals) - min(vals)) if len(vals) > 1 else 0.0, "n_judges": len(vals)}
    if extra:
        details.update(extra(ok))
    reasoning = " | ".join(f"{p['judge']}: {p.get('reasoning') or p.get('error')}" for p in per)
    return grader.score(sample, value, passed=passed, reasoning=reasoning[:2000], details=details, cost_usd=cost)


def _scale_value(data: dict, scale: str) -> float | None:
    if "pass" in data and scale == "pass_fail":
        return 1.0 if data["pass"] in (True, "true", "pass", 1) else 0.0
    s = data.get("score")
    if s is None:
        return 1.0 if data.get("pass") in (True, "true", 1) else 0.0 if "pass" in data else None
    try:
        s = float(s)
    except (TypeError, ValueError):
        return None
    if scale == "pass_fail":
        return 1.0 if s >= 1 else 0.0
    return (min(5.0, max(1.0, s)) - 1) / 4


@register("rubric")
class Rubric(Grader):
    """Pointwise rubric judge. scale: '1-5' (default, normalised to 0..1) or 'pass_fail'. anchors: optional text."""
    uses_model = True
    task = "rubric"

    def default_threshold(self) -> float:
        return 1.0 if self.p.get("scale") == "pass_fail" else 0.75

    def rubric_text(self) -> str:
        text = self.p.get("rubric", "Is the output correct, complete and helpful?")
        if self.p.get("anchors"):
            text += "\nAnchors:\n" + "\n".join(f"- {k}: {v}" for k, v in self.p["anchors"].items())
        return text

    async def grade(self, sample: Sample, case: Case) -> Score:
        if not sample.output:
            return self.score(sample, 0.0, reasoning="empty output")
        tok = self.ctx.tokenize
        scale = "pass_fail" if self.task in ("reference_match", "policy") else self.p.get("scale", "1-5")
        ref = self.reference_value(case) if self.p.get("use_reference", self.task == "reference_match") else None
        schema = '{"pass": true|false, "reasoning": "..."}' if scale == "pass_fail" else '{"score": <1-5>, "reasoning": "..."}'
        prompt = RUBRIC_PROMPT.format(
            task=self.task, scale=scale, rubric=self.rubric_text(), question=tok(case.input_text()), output=tok(sample.output),
            ref_hint=" and the REFERENCE answer" if ref else "", schema=schema,
            reference_block=f"<reference>\n{tok(str(ref))}\n</reference>\n" if ref else "")
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), lambda d: _scale_value(d, scale))


@register("reference_match")
class ReferenceMatch(Rubric):
    """Closed-QA: does the output state the same answer as the reference?"""
    task = "reference_match"

    def default_threshold(self) -> float:
        return 1.0

    def rubric_text(self) -> str:
        return self.p.get("rubric", "PASS if the output gives the same answer as the reference (wording may differ); FAIL if it "
                                    "contradicts, omits or hedges the key facts.")


@register("policy")
class Policy(Rubric):
    """Policy adherence: `policy` text describing what must never happen (quote banned phrases in "double quotes")."""
    task = "policy"

    def default_threshold(self) -> float:
        return 1.0

    def rubric_text(self) -> str:
        return "PASS if the output complies with the policy, FAIL if it violates it.\n<policy>" + self.p["policy"] + "</policy>"


@register("faithfulness")
class Faithfulness(Grader):
    """Claim-level groundedness against retrieved context: share of claims supported (contradictions weigh double)."""
    uses_model = True

    def default_threshold(self) -> float:
        return 0.9

    async def grade(self, sample: Sample, case: Case) -> Score:
        ref_ctx = dig(case.reference, "context")
        ctx_chunks = sample.context or ([ContextChunk(id="reference", text=str(ref_ctx))] if ref_ctx else [])
        if not ctx_chunks:
            return self.skip(sample, "no retrieved context on the sample (RAG graders need context)")
        if not sample.output:
            return self.score(sample, 0.0, reasoning="empty output")
        tok = self.ctx.tokenize
        context = "\n\n".join(f"[{c.id}] {tok(c.text)}" for c in ctx_chunks)
        prompt = FAITH_PROMPT.format(context=context, output=tok(sample.output))

        def value(d):
            claims = d.get("claims") or []
            if not claims:
                return 1.0
            sup = sum(c.get("verdict") == "supported" for c in claims)
            con = sum(c.get("verdict") == "contradicted" for c in claims)
            return max(0.0, (sup - con) / len(claims))

        def extra(ok):
            first = ok[0]["data"].get("claims", []) if ok else []
            return {"unsupported_claims": [c.get("claim") for c in first if c.get("verdict") != "supported"][:10]}
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), value, extra=extra)


@register("answer_relevance")
class AnswerRelevance(Grader):
    uses_model = True

    def default_threshold(self) -> float:
        return 0.75

    async def grade(self, sample: Sample, case: Case) -> Score:
        if not sample.output:
            return self.score(sample, 0.0, reasoning="empty output")
        tok = self.ctx.tokenize
        prompt = RELEVANCE_PROMPT.format(question=tok(case.input_text()), output=tok(sample.output))
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), lambda d: _scale_value(d, "1-5"))


@register("pairwise")
class Pairwise(Grader):
    """Compare this target's output with another target's output on the same case (against: <target>).
    Runs both orders (position swap); value 1 = win, 0.5 = tie, 0 = loss."""
    uses_model = True

    def default_threshold(self) -> float:
        return 0.5

    async def grade(self, sample: Sample, case: Case) -> Score:
        other_name = self.p.get("against")
        if not other_name or other_name == sample.target:
            return self.skip(sample, "pairwise needs `against: <other target>`")
        other = self.ctx.samples.get((other_name, case.id, sample.repeat)) or self.ctx.samples.get((other_name, case.id, 0))
        if other is None:
            return self.skip(sample, f"no sample from '{other_name}' for this case")
        tok = self.ctx.tokenize
        ref = self.reference_value(case) if self.p.get("use_reference") else None
        crit = self.p.get("criteria", "Which response better satisfies the user's request: correct, complete, concise, safe?")
        rb = f"<reference>\n{tok(str(ref))}\n</reference>\n" if ref else ""
        q = tok(case.input_text())
        mine, theirs = tok(sample.output or ""), tok(other.output or "")
        panel = JudgePanel(self, sample)
        r1, r2 = await asyncio.gather(panel.ask(PAIRWISE_PROMPT.format(criteria=crit, question=q, reference_block=rb, a=mine, b=theirs)),
                                      panel.ask(PAIRWISE_PROMPT.format(criteria=crit, question=q, reference_block=rb, a=theirs, b=mine)))
        merged = []
        for a, b in zip(r1, r2):
            if "data" in a and "data" in b:
                wa = {"A": 1.0, "B": 0.0}.get(str(a["data"].get("winner")).upper(), 0.5)
                wb = {"A": 0.0, "B": 1.0}.get(str(b["data"].get("winner")).upper(), 0.5)
                merged.append({"judge": a["judge"], "data": {"v": (wa + wb) / 2, "reasoning": a["data"].get("reasoning"),
                                                             "position_consistent": wa == wb},
                               "cost": a.get("cost", 0) + b.get("cost", 0)})
            else:
                merged.append({"judge": a["judge"], "error": a.get("error") or b.get("error")})
        return _combine(self, sample, merged, lambda d: d["v"], extra=lambda ok: {
            "against": other_name, "position_consistent": all(r["data"]["position_consistent"] for r in ok)})


async def judge_refusal(grader: Grader, sample: Sample) -> Score:
    prompt = REFUSAL_PROMPT.format(output=grader.ctx.tokenize(sample.output or ""))
    res = await JudgePanel(grader, sample).ask(prompt)
    return _combine(grader, sample, res, lambda d: 1.0 if d.get("refused") in (True, "true", 1) else 0.0)


__all__ = ["JudgePanel", "judge_refusal"]
