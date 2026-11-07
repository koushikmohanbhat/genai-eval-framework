"""Retrieval metrics (measured separately from answer quality) and citation / refusal checks."""
from __future__ import annotations

import math
import re

from ..models import Case, Sample, Score
from .base import Grader, dig, register

REFUSAL = re.compile(r"\b(i (?:can(?:no|')t|am unable|am not able)|i don't have (?:that|this|enough) information|not (?:able|allowed) to|"
                     r"cannot (?:help|share|provide|find)|outside (?:my|the) scope|not (?:covered|mentioned|stated) in|"
                     r"no information (?:about|on)|unable to find)", re.I)


def _gold(case: Case, key: str) -> list[str]:
    g = dig(case.reference, key) or []
    return [str(x) for x in (g if isinstance(g, list) else [g])]


class _Retrieval(Grader):
    def default_threshold(self) -> float:
        return 0.8

    def ids(self, sample: Sample) -> list[str]:
        return [c.id for c in sample.context]


@register("retrieval_recall_at_k")
class RecallAtK(_Retrieval):
    async def grade(self, sample: Sample, case: Case) -> Score:
        gold = _gold(case, self.p.get("reference_key", "gold_passages"))
        if not gold:
            return self.skip(sample, "no gold passages")
        k = int(self.p.get("k", 5))
        got = set(self.ids(sample)[:k])
        hit = [g for g in gold if g in got]
        return self.score(sample, len(hit) / len(gold), details={"k": k, "missing": [g for g in gold if g not in got]})


@register("mrr")
class MRR(_Retrieval):
    async def grade(self, sample: Sample, case: Case) -> Score:
        gold = set(_gold(case, self.p.get("reference_key", "gold_passages")))
        if not gold:
            return self.skip(sample, "no gold passages")
        rank = next((i for i, cid in enumerate(self.ids(sample), 1) if cid in gold), None)
        return self.score(sample, 1.0 / rank if rank else 0.0, details={"first_relevant_rank": rank})


@register("ndcg")
class NDCG(_Retrieval):
    async def grade(self, sample: Sample, case: Case) -> Score:
        gold = _gold(case, self.p.get("reference_key", "gold_passages"))
        if not gold:
            return self.skip(sample, "no gold passages")
        k = int(self.p.get("k", 5))
        gs = set(gold)
        dcg = sum(1 / math.log2(i + 1) for i, cid in enumerate(self.ids(sample)[:k], 1) if cid in gs)
        idcg = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(gold)) + 1))
        return self.score(sample, dcg / idcg if idcg else 0.0)


CITE = re.compile(r"\[([A-Za-z0-9_.:#/\-]+)\]")


@register("citation_accuracy")
class CitationAccuracy(Grader):
    """Citations like [doc-id] must point at retrieved chunks; with require_gold, at least one must be a gold passage."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        if case.reference.get("answerable") is False:
            return self.skip(sample, "unanswerable case")
        cited = CITE.findall(sample.output or "")
        retrieved = {c.id for c in sample.context}
        if not cited:
            return self.score(sample, 0.0 if self.p.get("require_citation", True) else 1.0, reasoning="no citations")
        valid = [c for c in cited if c in retrieved]
        value = len(valid) / len(cited)
        if self.p.get("require_gold"):
            gold = set(_gold(case, "gold_passages"))
            if gold and not (set(valid) & gold):
                value = min(value, 0.5)
        return self.score(sample, value, details={"cited": cited, "invalid": [c for c in cited if c not in retrieved]})


@register("correct_refusal")
class CorrectRefusal(Grader):
    """When case.reference.answerable is false the system must decline; when true it must not.
    Pattern-based by default; `use_judge: true` asks the judge panel instead."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        answerable = case.reference.get("answerable")
        if answerable is None:
            return self.skip(sample, "reference.answerable not set")
        if self.p.get("use_judge"):
            from .judges import judge_refusal
            s = await judge_refusal(self, sample)
            refused = (s.value or 0) >= 0.5
        else:
            refused = bool(REFUSAL.search(sample.output or ""))
        ok = refused != bool(answerable)
        return self.score(sample, 1.0 if ok else 0.0, details={"answerable": answerable, "refused": refused},
                          reasoning=("correctly declined" if refused else "correctly answered") if ok else
                          ("refused an answerable question" if refused else "answered a question the documents cannot answer"))
