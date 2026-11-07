"""String, structure and similarity graders. Free, fast, repeatable: always run these first."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

import jsonschema

from ..models import Case, Sample, Score
from .base import Grader, register


def _norm(s: Any, case_sensitive: bool = False) -> str:
    t = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    return t if case_sensitive else t.lower()


@register("exact")
class Exact(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        ref = self.reference_value(case)
        if ref is None:
            return self.skip(sample, "no reference")
        out = self.output_value(sample)
        cs = self.p.get("case_sensitive", False)
        ok = _norm(out, cs) == _norm(ref, cs)
        return self.score(sample, 1.0 if ok else 0.0, details={"expected": ref, "got": out})


@register("contains")
class Contains(Grader):
    """value: str | list; mode: any | all; negate: true for 'must not contain'."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        vals = self.p.get("value", self.reference_value(case, "contains"))
        if vals is None:
            return self.skip(sample, "nothing to look for")
        vals = [vals] if isinstance(vals, str) else list(vals)
        out = _norm(self.output_value(sample), self.p.get("case_sensitive", False))
        found = [v for v in vals if _norm(v, self.p.get("case_sensitive", False)) in out]
        if self.p.get("negate"):
            return self.score(sample, 0.0 if found else 1.0, details={"forbidden_found": found})
        frac = len(found) / len(vals)
        ok = frac == 1.0 if self.p.get("mode", "all") == "all" else bool(found)
        return self.score(sample, frac, passed=ok, details={"found": found, "missing": [v for v in vals if v not in found]})


@register("regex")
class Regex(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        pat = re.compile(self.p["pattern"], 0 if self.p.get("case_sensitive") else re.I)
        m = bool(pat.search(str(self.output_value(sample) or "")))
        ok = not m if self.p.get("negate") else m
        return self.score(sample, 1.0 if ok else 0.0)


@register("json_schema")
class JsonSchema(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        schema = self.p.get("schema")
        if isinstance(schema, str):
            schema = json.loads(self.ctx.suite.path(schema).read_text(encoding="utf-8"))
        data = sample.output_json
        if data is None:
            try:
                data = json.loads(sample.output or "")
            except json.JSONDecodeError:
                return self.score(sample, 0.0, reasoning="output is not valid JSON")
        errors = sorted(jsonschema.Draft202012Validator(schema or {}).iter_errors(data), key=lambda e: list(e.path))
        return self.score(sample, 0.0 if errors else 1.0, details={"errors": [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:10]]})


# ---- field match with normalisers --------------------------------------------
def _digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _amount(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").lower().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(k|lakh|lakhs|lac|crore|cr|m)?", s)
    if not m:
        return None
    n = float(m.group(1))
    mult = {"k": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "crore": 1e7, "cr": 1e7, "m": 1e6}.get(m.group(2) or "", 1)
    return n * mult


DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d.%m.%Y", "%Y/%m/%d")


def _date(v: Any) -> str | None:
    s = str(v or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return s or None


NORMALISERS = {
    "exact": lambda v: v,
    "text": lambda v: _norm(v),
    "lower": lambda v: _norm(v),
    "digits": _digits,
    "phone": lambda v: _digits(v)[-10:],
    "amount": _amount,
    "date": _date,
    "name": lambda v: " ".join(sorted(_norm(v).replace(".", " ").split())),
    "upper_alnum": lambda v: re.sub(r"[^A-Z0-9]", "", str(v or "").upper()),
}


def _set_f1(got: list, exp: list) -> float:
    g, e = Counter(map(_norm, got or [])), Counter(map(_norm, exp or []))
    if not g and not e:
        return 1.0
    tp = sum((g & e).values())
    if tp == 0:
        return 0.0
    p, r = tp / sum(g.values()), tp / sum(e.values())
    return 2 * p * r / (p + r)


@register("field_match")
class FieldMatch(Grader):
    """Field-level accuracy for extraction. fields: {name: normaliser | {normaliser, tolerance, weight}}.
    Reference fields come from case.reference[reference_key] (default 'fields'); lists are scored with F1."""

    def default_threshold(self) -> float:
        return 0.999

    async def grade(self, sample: Sample, case: Case) -> Score:
        expected = self.reference_value(case, "fields")
        if not isinstance(expected, dict):
            return self.skip(sample, "no reference fields")
        got = self.output_value(sample) if self.p.get("output_path") else sample.output_json
        if not isinstance(got, dict):
            return self.score(sample, 0.0, reasoning="output is not a JSON object", details={"per_field": {}})
        spec = self.p.get("fields") or {k: "text" for k in expected}
        per, total_w, acc = {}, 0.0, 0.0
        for fname, fs in spec.items():
            fs = {"normaliser": fs} if isinstance(fs, str) else dict(fs)
            if fname not in expected:
                continue
            w = float(fs.get("weight", 1.0))
            e, g = expected.get(fname), got.get(fname)
            if isinstance(e, list) or isinstance(g, list):
                v = _set_f1(g if isinstance(g, list) else [g], e if isinstance(e, list) else [e])
            else:
                n = NORMALISERS[fs.get("normaliser", "text")]
                ne, ng = n(e), n(g)
                if isinstance(ne, float) and isinstance(ng, float):
                    tol = float(fs.get("tolerance", 0.0))
                    v = 1.0 if abs(ne - ng) <= tol * max(1.0, abs(ne)) else 0.0
                else:
                    v = 1.0 if ne == ng and ne not in (None, "") or (e in (None, "") and g in (None, "")) else 0.0
            per[fname] = {"score": v, "expected": e, "got": g}
            total_w += w
            acc += w * v
        value = acc / total_w if total_w else 0.0
        wrong = [k for k, d in per.items() if d["score"] < 1]
        return self.score(sample, value, details={"per_field": per, "wrong_fields": wrong},
                          reasoning=f"{len(per) - len(wrong)}/{len(per)} fields correct")


@register("numeric_tolerance")
class NumericTolerance(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        e, g = _amount(self.reference_value(case)), _amount(self.output_value(sample))
        if e is None:
            return self.skip(sample, "no numeric reference")
        if g is None:
            return self.score(sample, 0.0, reasoning="no number in output")
        tol = float(self.p.get("tolerance", 0.01))
        ok = abs(e - g) <= tol * max(1.0, abs(e)) if self.p.get("relative", True) else abs(e - g) <= tol
        return self.score(sample, 1.0 if ok else 0.0, details={"expected": e, "got": g})


# ---- similarity -----------------------------------------------------------
def _tokens(s: str) -> list[str]:
    return re.findall(r"\w+", (s or "").lower())


def rouge_l(cand: str, ref: str) -> float:
    a, b = _tokens(cand), _tokens(ref)
    if not a or not b:
        return 0.0
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, 1):
            cur = dp[j]
            dp[j] = prev + 1 if x == y else max(dp[j], dp[j - 1])
            prev = cur
    lcs = dp[-1]
    if lcs == 0:
        return 0.0
    p, r = lcs / len(a), lcs / len(b)
    return 2 * p * r / (p + r)


def bleu(cand: str, ref: str, n: int = 4) -> float:
    c, r = _tokens(cand), _tokens(ref)
    if not c or not r:
        return 0.0
    logs = 0.0
    for k in range(1, n + 1):
        cc = Counter(tuple(c[i:i + k]) for i in range(len(c) - k + 1))
        rc = Counter(tuple(r[i:i + k]) for i in range(len(r) - k + 1))
        match = sum((cc & rc).values())
        logs += math.log((match + 1) / (max(1, sum(cc.values())) + 1))  # add-one smoothing
    bp = 1.0 if len(c) > len(r) else math.exp(1 - len(r) / len(c))
    return bp * math.exp(logs / n)


@register("rouge")
class Rouge(Grader):
    def default_threshold(self) -> float:
        return 0.5

    async def grade(self, sample: Sample, case: Case) -> Score:
        ref = self.reference_value(case)
        if not ref:
            return self.skip(sample, "no reference")
        return self.score(sample, rouge_l(str(self.output_value(sample) or ""), str(ref)))


@register("bleu")
class Bleu(Grader):
    def default_threshold(self) -> float:
        return 0.3

    async def grade(self, sample: Sample, case: Case) -> Score:
        ref = self.reference_value(case)
        if not ref:
            return self.skip(sample, "no reference")
        return self.score(sample, bleu(str(self.output_value(sample) or ""), str(ref)))


@register("embedding_similarity")
class EmbeddingSimilarity(Grader):
    uses_model = True

    def default_threshold(self) -> float:
        return 0.8

    async def grade(self, sample: Sample, case: Case) -> Score:
        ref = self.reference_value(case)
        if not ref:
            return self.skip(sample, "no reference")
        prof = self.p.get("profile") or self.ctx.suite.embeddings
        if not prof:
            return self.skip(sample, "no embeddings profile configured (suite.embeddings)")
        tok = self.ctx.tokenize
        a, b = await self.ctx.pool.get(prof).embed([tok(str(self.output_value(sample) or "")), tok(str(ref))])
        dot = sum(x * y for x, y in zip(a, b))
        na, nb = math.sqrt(sum(x * x for x in a)) or 1, math.sqrt(sum(y * y for y in b)) or 1
        return self.score(sample, max(0.0, dot / (na * nb)))
