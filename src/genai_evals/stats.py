"""Aggregation with honest uncertainty: per-case means, bootstrap CIs over cases, pass@k / pass^k, paired comparisons."""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any

from .models import Case, Sample, Score

B = 2000


def bootstrap_ci(values: list[float], seed: int = 7, alpha: float = 0.05, b: int = B) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return (means[int(alpha / 2 * b)], means[min(b - 1, int((1 - alpha / 2) * b))])


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar test on discordant pairs (b: A pass/B fail, c: A fail/B pass)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_delta(cur: dict[str, float], base: dict[str, float], seed: int = 7, cur_pass: dict[str, bool] | None = None,
                 base_pass: dict[str, bool] | None = None, alpha: float = 0.05) -> dict[str, Any]:
    common = sorted(set(cur) & set(base))
    if not common:
        return {"n": 0, "delta": 0.0, "ci_low": 0.0, "ci_high": 0.0, "significant": False, "note": "no overlapping cases"}
    diffs = [cur[c] - base[c] for c in common]
    lo, hi = bootstrap_ci(diffs, seed, alpha=alpha)
    out: dict[str, Any] = {"n": len(common), "delta": statistics.fmean(diffs), "ci_low": lo, "ci_high": hi,
                           "significant": (lo > 0 or hi < 0) and any(d != 0 for d in diffs),
                           "improved": [c for c in common if cur[c] - base[c] > 1e-9],
                           "regressed": [c for c in common if base[c] - cur[c] > 1e-9]}
    if cur_pass is not None and base_pass is not None:
        b = sum(1 for c in common if cur_pass.get(c) and not base_pass.get(c))
        cc = sum(1 for c in common if not cur_pass.get(c) and base_pass.get(c))
        out["mcnemar_p"] = mcnemar_p(b, cc)
    if len(common) < 30:
        out["warning"] = f"only {len(common)} paired cases: differences under ~{min(1.0, 2 / math.sqrt(len(common))):.0%} may not be detectable"
    return out


def summarise(target: str, cases: list[Case], samples: list[Sample], scores: list[Score], seed: int = 7,
              on_error: str = "fail") -> dict[str, Any]:
    """Aggregate one target's scores.

    Skipped scores (grader not applicable) are excluded. Errored scores (grader or target failed) count as a failure
    with value 0 when ``on_error="fail"`` (the default), so a grader that errors on hard cases cannot raise the mean;
    with ``on_error="exclude"`` they are left out and only counted.
    """
    by_case = {c.id: c for c in cases}
    graders: dict[str, Any] = {}
    sc_by_g: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        if s.target == target:
            sc_by_g[s.grader].append(s)
    for g, lst in sc_by_g.items():
        per_case_vals: dict[str, list[float]] = defaultdict(list)
        per_case_pass: dict[str, list[bool]] = defaultdict(list)
        skipped = sum(1 for s in lst if s.skipped)
        errors = sum(1 for s in lst if s.error)
        for s in lst:
            if s.skipped:
                continue
            if s.error and on_error == "fail":
                per_case_vals[s.case_id].append(0.0)
                per_case_pass[s.case_id].append(False)
                continue
            if s.value is None:
                continue
            per_case_vals[s.case_id].append(s.value)
            per_case_pass[s.case_id].append(bool(s.passed))
        case_means = {c: statistics.fmean(v) for c, v in per_case_vals.items()}
        case_pass = {c: all(p) for c, p in per_case_pass.items()}
        vals = list(case_means.values())
        lo, hi = bootstrap_ci(vals, seed)
        allp = [p for ps in per_case_pass.values() for p in ps]
        graders[g] = {
            "mean": statistics.fmean(vals) if vals else None, "ci_low": lo if vals else None, "ci_high": hi if vals else None,
            "pass_rate": (sum(allp) / len(allp)) if allp else None,
            "pass_at_k": (sum(any(p) for p in per_case_pass.values()) / len(per_case_pass)) if per_case_pass else None,
            "pass_hat_k": (sum(all(p) for p in per_case_pass.values()) / len(per_case_pass)) if per_case_pass else None,
            "n": len(vals), "skipped": skipped, "errors": errors, "per_case": case_means, "per_case_pass": case_pass,
            "judge_cost_usd": sum(s.cost_usd for s in lst),
            "mean_disagreement": (statistics.fmean([s.details["disagreement"] for s in lst if "disagreement" in s.details])
                                  if any("disagreement" in s.details for s in lst) else None),
        }
    mine = [s for s in samples if s.target == target]
    lat = [s.latency_ms for s in mine if s.latency_ms is not None]
    ttft = [s.ttft_ms for s in mine if s.ttft_ms is not None]
    costs = [s.cost_usd for s in mine if s.cost_usd is not None]
    toks = [s.usage.total for s in mine]
    ops = {
        "latency_ms": {"mean": statistics.fmean(lat) if lat else None, "p50": percentile(lat, .5), "p95": percentile(lat, .95), "p99": percentile(lat, .99)},
        "ttft_ms": {"mean": statistics.fmean(ttft) if ttft else None, "p50": percentile(ttft, .5), "p95": percentile(ttft, .95), "p99": percentile(ttft, .99)},
        "cost_usd": {"mean": statistics.fmean(costs) if costs else None, "total": sum(costs) if costs else None, "p95": percentile(costs, .95)},
        "tokens": {"mean": statistics.fmean(toks) if toks else None, "total": sum(toks), "p95": percentile([float(t) for t in toks], .95)},
        "steps": {"mean": statistics.fmean([s.steps for s in mine]) if mine else None},
        "errors": sum(1 for s in mine if s.error), "samples": len(mine),
    }
    # slices by tag
    slices: dict[str, dict[str, float]] = defaultdict(dict)
    slice_n: dict[str, dict[str, int]] = defaultdict(dict)
    for g, d in graders.items():
        by_tag: dict[str, list[float]] = defaultdict(list)
        for cid, v in d["per_case"].items():
            for t in by_case.get(cid, Case(id=cid, input="")).tags:
                by_tag[t].append(v)
        for t, vs in by_tag.items():
            slices[t][g] = statistics.fmean(vs)
            slice_n[t][g] = len(vs)
    # critical cases: every non-skipped score must pass on every repeat
    crit_ids = [c.id for c in cases if c.critical]
    failed = sorted({s.case_id for s in scores if s.target == target and s.case_id in crit_ids and not s.skipped
                     and (s.passed is False or (s.error and on_error == "fail"))}
                    | {s.case_id for s in mine if s.case_id in crit_ids and s.error})
    return {"target": target, "graders": graders, "ops": ops, "slices": dict(slices), "slice_n": dict(slice_n),
            "critical": {"total": len(crit_ids), "failed": failed, "passed": len(crit_ids) - len(failed)}}


def metric_series(summary: dict[str, Any], samples: list[Sample], metric: str) -> list[float]:
    mine = [s for s in samples if s.target == summary["target"]]
    if metric in ("latency_ms", "ttft_ms", "cost_usd"):
        return [getattr(s, metric) for s in mine if getattr(s, metric) is not None]
    if metric == "tokens":
        return [float(s.usage.total) for s in mine]
    if metric == "steps":
        return [float(s.steps) for s in mine]
    raise KeyError(metric)
