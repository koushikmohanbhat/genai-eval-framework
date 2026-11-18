"""LLM-as-judge graders, always run through an explicitly configured judge panel (one or more provider profiles).

Hygiene enforced here (see OpenAI's evaluation best practices): temperature 0; structured JSON verdicts in which a
short, checkable rationale (the evidence that decides it, quoted briefly) comes before the verdict, rather than a
free-form chain of thought; an instruction to judge substance rather than length; optional "judge must be a
different model family than the target"; position swap for pairwise, with the length of both answers recorded so
length bias can be measured; per-judge results and disagreement recorded on every score.
"""
from __future__ import annotations

import asyncio
import statistics
from typing import Any

from ..models import Case, ContextChunk, Message, Sample, Score
from ..targets.base import parse_json
from .base import Grader, dig, register

JUDGE_RULES = """<rules>
- Put "rationale" BEFORE the verdict: at most two short sentences naming the evidence that decides it, quoting the
  deciding words from the output, reference or context briefly. Do not write out your step-by-step thinking; the
  rationale is stored and shown to reviewers, who must be able to check it against the texts.
- Do not repeat personal details (names, numbers, addresses) in the rationale; refer to them ("the phone number").
- Judge substance, not length or style: a longer answer is not better because it is longer. Do not reward padding,
  repetition or restating the question; an answer that is complete and concise beats one that is long and complete.
- Use only the information given here. If it does not let you decide, give "unknown" as the verdict and say in the
  rationale what is missing. Do not guess.
</rules>"""

TURN_PROMPT = """<task>{task}</task>
You are a strict, impartial evaluator. Grade one assistant TURN of a conversation against the RUBRIC. Earlier turns are
given as CONVERSATION for context only: grade the TURN, not the earlier turns.
<scale>{scale}</scale>
<rubric>
{rubric}
</rubric>
<conversation>
{conversation}
</conversation>
<output>
{output}
</output>
{rules}
Respond with JSON only: {schema}"""

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
{rules}
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
For each claim, give the evidence before the verdict: a short quote from the CONTEXT (at most 20 words), or "none".
Respond with JSON only: {{"claims": [{{"claim": "...", "evidence": "...", "verdict": "supported|contradicted|unsupported"}}]}}"""

RELEVANCE_PROMPT = """<task>answer_relevance</task>
Rate how directly and completely the OUTPUT answers the QUESTION (1 = irrelevant, 5 = fully answers it). Do not judge correctness.
<question>
{question}
</question>
<output>
{output}
</output>
{rules}
Respond with JSON only: {{"rationale": "...", "score": <1-5> or "unknown"}}"""

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
{rules}
Respond with JSON only: {{"rationale": "...", "winner": "A|B|tie"}}"""

CONTEXT_PRECISION_PROMPT = """<task>context_precision</task>
For each retrieved CHUNK, decide whether it is useful for answering the QUESTION{ref_hint}. A chunk is useful if it
contains information needed for a correct answer; topical but unhelpful chunks are not useful.
<question>
{question}
</question>
{reference_block}<chunks>
{chunks}
</chunks>
For each chunk, give a short reason before the verdict (at most one sentence, quoting the deciding words).
Respond with JSON only: {{"chunks": [{{"id": "...", "reason": "...", "useful": true|false}}]}}"""

CONTEXT_RECALL_PROMPT = """<task>context_recall</task>
Split the REFERENCE answer into its distinct factual statements. For each, decide whether the retrieved CONTEXT
contains the information needed to make that statement (it need not be worded the same way).
<question>
{question}
</question>
<reference>
{reference}
</reference>
<context>
{context}
</context>
For each statement, give the evidence before the verdict: a short quote from the CONTEXT (at most 20 words), or "none".
Respond with JSON only: {{"statements": [{{"statement": "...", "evidence": "...", "attributed": true|false}}]}}"""

REFUSAL_PROMPT = """<task>refusal</task>
Did the OUTPUT decline to answer or say the information is not available (rather than answering)?
<output>
{output}
</output>
Respond with JSON only: {{"rationale": "...", "refused": true|false}}"""


JUDGE_GRADER_TYPES = {"rubric", "reference_match", "policy", "faithfulness", "answer_relevance", "pairwise",
                      "context_precision", "context_recall"}


def judge_prompts_hash() -> str:
    """Fingerprint of the judge prompt templates, recorded with every run so judge changes are visible over time."""
    import hashlib
    blob = "\0".join(v for k, v in sorted(globals().items()) if k.endswith(("_PROMPT", "_RULES")) and isinstance(v, str))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


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
                return {"judge": name, "data": data, "cost": prov.cost(r.usage) or 0.0,
                        "model": r.model or prov.profile.model}
            except Exception as e:  # noqa: BLE001
                return {"judge": name, "error": f"{type(e).__name__}: {e}"}
        return await asyncio.gather(*[one(n) for n in self.names])


DEFAULT_MAX_RATIONALE = 300


def _rationale(data: dict, policy: dict) -> str | None:
    """The judge's short rationale, capped (judge_policy.max_rationale_chars, default 300) or dropped
    (judge_policy.store_rationale: false). Stored rationales go through the run's tokeniser like any other text."""
    if policy.get("store_rationale", True) is False:
        return None
    text = data.get("rationale") or data.get("reasoning")
    if not text:
        return None
    text = " ".join(str(text).split())
    cap = int(policy.get("max_rationale_chars", DEFAULT_MAX_RATIONALE))
    return text if len(text) <= cap else text[: max(0, cap - 1)].rstrip() + "…"


def _combine(grader: Grader, sample: Sample, results: list[dict], to_value, reasoning_key: str = "rationale", extra=None) -> Score:
    ok = [r for r in results if "data" in r]
    policy = grader.ctx.suite.judge_policy or {}
    per = []
    for r in results:
        if "data" in r:
            v = UNKNOWN if _is_unknown(r["data"]) else to_value(r["data"])
            d = r["data"] if reasoning_key in ("rationale", "reasoning") else {"rationale": r["data"].get(reasoning_key)}
            entry = {"judge": r["judge"], "model": r.get("model"), "reasoning": _rationale(d, policy)}
            entry.update({"unknown": True} if v == UNKNOWN else {"value": v})
            per.append(entry)
        else:
            per.append({"judge": r["judge"], "error": r["error"]})
    cost = sum(r.get("cost", 0.0) for r in results)
    vals = [p["value"] for p in per if p.get("value") is not None]
    if not vals and any(p.get("unknown") for p in per):
        # the judges that answered could not decide from the texts given: not a pass, not a fail, not an error.
        # Recorded as a skip with label "unknown", so it is left out of means, counted, and queued for human review.
        why = " | ".join(f"{p['judge']}: {p.get('reasoning') or p.get('error')}" for p in per if p.get("reasoning") or p.get("error"))
        return Score(grader=grader.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                     skipped=True, label=UNKNOWN, skip_reason="judge could not decide from the texts given",
                     reasoning=why[:2000], details={"judges": per, "unknown": True}, cost_usd=cost)
    if not vals:
        from ..retry import classify_error_text
        msg = "; ".join(p.get("error", "") for p in per)
        sc = Score(grader=grader.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                   error=msg, details={"judges": per}, cost_usd=cost)
        sc.error_kind = classify_error_text(msg)                                # type: ignore[attr-defined]
        return sc
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
    if len(vals) > 1:
        details["split_verdict"] = len({v >= thr for v in vals}) > 1     # the judges disagree on pass / fail
    if any(p.get("unknown") for p in per):
        details["unknown_judges"] = [p["judge"] for p in per if p.get("unknown")]
    if extra:
        details.update(extra(ok))
    reasoning = " | ".join(
        f"{p['judge']}: {p.get('reasoning') or p.get('error')}" for p in per if p.get("reasoning") or p.get("error")
    )
    return grader.score(sample, value, passed=passed, reasoning=reasoning[:2000], details=details, cost_usd=cost)


UNKNOWN = "unknown"


def _is_unknown(data: dict) -> bool:
    return any(str(data.get(k, "")).strip().lower() == UNKNOWN for k in ("pass", "score", "verdict", "winner"))


def _scale_value(data: dict, scale: str) -> float | str | None:
    if _is_unknown(data):
        return UNKNOWN
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
        schema = ('{"rationale": "...", "pass": true|false|"unknown"}' if scale == "pass_fail"
                  else '{"rationale": "...", "score": <1-5> or "unknown"}')
        if self.p.get("per_turn"):
            return await self._grade_turns(sample, case, scale, ref, schema)
        prompt = RUBRIC_PROMPT.format(
            task=self.task, scale=scale, rubric=self.rubric_text(), question=tok(case.input_text()), output=tok(sample.output),
            rules=JUDGE_RULES,
            ref_hint=" and the REFERENCE answer" if ref else "", schema=schema,
            reference_block=f"<reference>\n{tok(str(ref))}\n</reference>\n" if ref else "")
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), lambda d: _scale_value(d, scale))


    async def _grade_turns(self, sample: Sample, case: Case, scale: str, ref: Any, schema: str) -> Score:
        """per_turn: true — judge every assistant turn of the conversation (with the earlier turns as context) and pass
        only if every turn passes. One bad turn fails the conversation, so the grader's pass rate is the share of
        conversations that stayed acceptable throughout. value = the lowest turn value."""
        tok = self.ctx.tokenize
        msgs = [m for m in sample.messages if m.role in ("user", "assistant") and (m.content or "").strip()]
        turns = [i for i, m in enumerate(msgs) if m.role == "assistant"]
        if not turns:
            if not sample.output:
                return self.score(sample, 0.0, reasoning="empty output")
            msgs = [Message(role="user", content=case.input_text()), Message(role="assistant", content=sample.output)]
            turns = [1]
        lim = int(self.p.get("max_turns", 20))
        if len(turns) > lim:
            turns = turns[-lim:]

        async def one(i: int) -> Score:
            convo = "\n".join(f"{m.role.upper()}: {tok(m.content or '')}" for m in msgs[:i])
            prompt = TURN_PROMPT.format(task=self.task, scale=scale, rubric=self.rubric_text()
                                        + (f"\nREFERENCE: {tok(str(ref))}" if ref else ""),
                                        conversation=convo or "(none)", output=tok(msgs[i].content or ""),
                                        rules=JUDGE_RULES, schema=schema)
            return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), lambda d: _scale_value(d, scale))
        res = await asyncio.gather(*[one(i) for i in turns])
        per_turn = [{"turn": n + 1, "value": r.value, "passed": r.passed, "unknown": r.label == UNKNOWN,
                     "error": r.error, "reasoning": r.reasoning} for n, r in enumerate(res)]
        cost = sum(r.cost_usd for r in res)
        failed = [t for t in per_turn if t["passed"] is False]
        details = {"per_turn": True, "turns": per_turn, "n_turns": len(per_turn),
                   "first_failing_turn": failed[0]["turn"] if failed else None}
        if failed:                                       # one bad turn fails the conversation, whatever else happened
            return self.score(sample, min(t["value"] for t in failed if t["value"] is not None), passed=False,
                              reasoning=f"turn {failed[0]['turn']}: {failed[0]['reasoning'] or ''}"[:2000],
                              details=details, cost_usd=cost)
        errs = [r for r in res if r.error]
        if errs:
            from ..retry import classify_error_text
            msg = "; ".join(r.error for r in errs)
            sc = Score(grader=self.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                       error=msg, details=details, cost_usd=cost)
            sc.error_kind = classify_error_text(msg)                            # type: ignore[attr-defined]
            return sc
        if any(t["unknown"] for t in per_turn):
            return Score(grader=self.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                         skipped=True, label=UNKNOWN, skip_reason="judge could not decide on at least one turn",
                         details=details | {"unknown": True}, cost_usd=cost)
        return self.score(sample, min(t["value"] for t in per_turn), passed=True,
                          reasoning=f"all {len(per_turn)} turns acceptable", details=details, cost_usd=cost)


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
        prompt = RELEVANCE_PROMPT.format(question=tok(case.input_text()), output=tok(sample.output), rules=JUDGE_RULES)
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), lambda d: _scale_value(d, "1-5"))


def _retrieved(sample: Sample) -> list[ContextChunk]:
    return list(sample.context or [])


def _truthy(v: Any) -> bool:
    return v in (True, "true", "yes", 1, "1")


@register("context_precision")
class ContextPrecision(Grader):
    """Are the retrieved chunks useful, and are the useful ones ranked first? Judge-labelled usefulness per chunk,
    scored as average precision (mean of precision@k at each useful chunk), so useful chunks ranked low cost more.
    Uses the reference answer when the case has one (use_reference: false to turn off). No document relevance labels
    needed (compare retrieval_recall_at_k / ndcg, which need them)."""
    uses_model = True

    def default_threshold(self) -> float:
        return 0.7

    async def grade(self, sample: Sample, case: Case) -> Score:
        chunks = _retrieved(sample)[: int(self.p.get("k", 10))]
        if not chunks:
            return self.skip(sample, "no retrieved context on the sample (RAG graders need context)")
        tok = self.ctx.tokenize
        ref = self.reference_value(case) if self.p.get("use_reference", True) else None
        prompt = CONTEXT_PRECISION_PROMPT.format(
            question=tok(case.input_text()), ref_hint=" (the REFERENCE shows what a correct answer contains)" if ref else "",
            reference_block=f"<reference>\n{tok(str(ref))}\n</reference>\n" if ref else "",
            chunks="\n\n".join(f"<chunk id=\"{c.id}\">\n{tok(c.text)}\n</chunk>" for c in chunks))
        order = [c.id for c in chunks]

        def value(d):
            verdict = {str(c.get("id")): _truthy(c.get("useful")) for c in d.get("chunks") or []}
            flags = [verdict.get(str(i), False) for i in order]
            hits, ap = 0, 0.0
            for k, f in enumerate(flags, 1):
                if f:
                    hits += 1
                    ap += hits / k
            return ap / hits if hits else 0.0

        def extra(ok):
            first = ok[0]["data"].get("chunks", []) if ok else []
            return {"useful": [c.get("id") for c in first if _truthy(c.get("useful"))], "k": len(order)}
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), value, extra=extra)


@register("context_recall")
class ContextRecall(Grader):
    """Did retrieval bring back everything a correct answer needs? Share of the reference answer's statements that
    the retrieved context supports. Needs a reference answer (reference_key, default `answer`)."""
    uses_model = True

    def default_threshold(self) -> float:
        return 0.85

    async def grade(self, sample: Sample, case: Case) -> Score:
        ref = self.reference_value(case)
        if not ref:
            return self.skip(sample, "context_recall needs a reference answer")
        chunks = _retrieved(sample)
        if not chunks:
            return self.score(sample, 0.0, reasoning="nothing retrieved")
        tok = self.ctx.tokenize
        prompt = CONTEXT_RECALL_PROMPT.format(question=tok(case.input_text()), reference=tok(str(ref)),
                                              context="\n\n".join(f"[{c.id}] {tok(c.text)}" for c in chunks))

        def value(d):
            st = d.get("statements") or []
            return (sum(_truthy(x.get("attributed")) for x in st) / len(st)) if st else 0.0

        def extra(ok):
            first = ok[0]["data"].get("statements", []) if ok else []
            return {"missing": [x.get("statement") for x in first if not _truthy(x.get("attributed"))][:10]}
        return _combine(self, sample, await JudgePanel(self, sample).ask(prompt), value, extra=extra)


@register("pairwise")
class Pairwise(Grader):
    """Compare this target's output with another target's output on the same case (against: <target>).
    Runs both orders (position swap); value 1 = win, 0.5 = tie, 0 = loss.

    Length bias: every score records both answers' word counts, their ratio and whether the longer answer won, and the
    run summary reports `longer_win_rate` (share of decided comparisons won by the longer answer). A rate far above
    50% when answers differ a lot in length is a sign the judge rewards length. `max_length_ratio` (optional) marks
    comparisons where one answer is more than that many times longer as `length_confounded`."""
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
        r1, r2 = await asyncio.gather(
            panel.ask(PAIRWISE_PROMPT.format(criteria=crit, question=q, reference_block=rb, a=mine, b=theirs, rules=JUDGE_RULES)),
            panel.ask(PAIRWISE_PROMPT.format(criteria=crit, question=q, reference_block=rb, a=theirs, b=mine, rules=JUDGE_RULES)))
        merged = []
        for a, b in zip(r1, r2):
            if "data" in a and "data" in b:
                wa = {"A": 1.0, "B": 0.0}.get(str(a["data"].get("winner")).upper(), 0.5)
                wb = {"A": 0.0, "B": 1.0}.get(str(b["data"].get("winner")).upper(), 0.5)
                merged.append({"judge": a["judge"], "data": {"v": (wa + wb) / 2,
                                                             "rationale": a["data"].get("rationale") or a["data"].get("reasoning"),
                                                             "position_consistent": wa == wb},
                               "cost": a.get("cost", 0) + b.get("cost", 0)})
            else:
                merged.append({"judge": a["judge"], "error": a.get("error") or b.get("error")})
        lm, lt = len((sample.output or "").split()), len((other.output or "").split())
        ratio = (max(lm, lt) / max(1, min(lm, lt))) if (lm or lt) else 1.0
        max_ratio = self.p.get("max_length_ratio")

        def extra(ok):
            vals = [r["data"]["v"] for r in ok]
            v = statistics.fmean(vals) if vals else 0.5
            decided = v != 0.5 and lm != lt
            return {"against": other_name, "position_consistent": all(r["data"]["position_consistent"] for r in ok),
                    "length_words": [lm, lt], "length_ratio": round(ratio, 3),
                    "longer_won": ((v > 0.5) == (lm > lt)) if decided else None,
                    "length_confounded": bool(max_ratio and ratio > float(max_ratio))}
        return _combine(self, sample, merged, lambda d: d["v"], extra=extra)


async def judge_refusal(grader: Grader, sample: Sample) -> Score:
    prompt = REFUSAL_PROMPT.format(output=grader.ctx.tokenize(sample.output or ""))  # no length rule: refusal is binary
    res = await JudgePanel(grader, sample).ask(prompt)
    return _combine(grader, sample, res, lambda d: 1.0 if d.get("refused") in (True, "true", 1) else 0.0)


__all__ = ["JUDGE_RULES", "JudgePanel", "judge_refusal"]
