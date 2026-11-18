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


def bootstrap_p(diffs: list[float], seed: int = 7, b: int = B) -> float:
    """Two-sided bootstrap p-value for a mean paired difference of zero: twice the share of resampled means on the
    other side of zero (floored at 1/b). Used for multiple-comparison correction across gates."""
    n = len(diffs)
    if n < 2 or all(d == 0 for d in diffs):
        return 1.0
    rng = random.Random(seed)
    means = [sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(b)]
    le = sum(1 for m in means if m <= 0) / b
    ge = sum(1 for m in means if m >= 0) / b
    return max(1.0 / b, min(1.0, 2 * min(le, ge)))


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (step-down, monotone). Controls the chance of any false 'significant'
    across the family at alpha, with more power than Bonferroni."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


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
                           "p_value": bootstrap_p(diffs, seed),
                           "improved": [c for c in common if cur[c] - base[c] > 1e-9],
                           "regressed": [c for c in common if base[c] - cur[c] > 1e-9]}
    if cur_pass is not None and base_pass is not None:
        b = sum(1 for c in common if cur_pass.get(c) and not base_pass.get(c))
        cc = sum(1 for c in common if not cur_pass.get(c) and base_pass.get(c))
        out["mcnemar_p"] = mcnemar_p(b, cc)
    if len(common) < 30:
        out["warning"] = f"only {len(common)} paired cases: differences under ~{min(1.0, 2 / math.sqrt(len(common))):.0%} may not be detectable"
    return out


SPLITS = ("representative", "stress")


def split_of(case: Case, stress_tags: list[str] | tuple[str, ...] = ("edge", "adversarial")) -> str:
    """representative (production-like traffic) or stress (edge / adversarial cases written to find failures).
    An explicit `split` on the case wins; otherwise a case with any of the stress tags is stress."""
    if case.split in SPLITS:
        return case.split
    return "stress" if set(case.tags or []) & set(stress_tags) else "representative"


def weighted_mean(values: list[float], weights: list[float]) -> float | None:
    tot = sum(weights)
    return (sum(v * w for v, w in zip(values, weights)) / tot) if values and tot > 0 else None


def weighted_bootstrap_ci(values: list[float], weights: list[float], seed: int = 7, alpha: float = 0.05,
                          b: int = B) -> tuple[float, float]:
    """Bootstrap CI for a weighted mean: resample cases (with their weights)."""
    if not values:
        return (float("nan"), float("nan"))
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    ms = []
    for _ in range(b):
        idx = [rng.randrange(n) for _ in range(n)]
        m = weighted_mean([values[i] for i in idx], [weights[i] for i in idx])
        if m is not None:
            ms.append(m)
    ms.sort()
    return (ms[int(alpha / 2 * len(ms))], ms[min(len(ms) - 1, int((1 - alpha / 2) * len(ms)))])


def _split_stats(per_case: dict[str, float], per_case_pass_all: dict[str, list[bool]], by_case: dict[str, Case],
                 stress_tags, seed: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sp in SPLITS:
        ids = [c for c in per_case if split_of(by_case.get(c, Case(id=c, input="")), stress_tags) == sp]
        if not ids:
            continue
        vals = [per_case[c] for c in ids]
        ws = [float(by_case[c].weight) if c in by_case and by_case[c].weight is not None else 1.0 for c in ids]
        weighted = sp == "representative" and any(c in by_case and by_case[c].weight is not None for c in ids)
        if weighted:
            mean, (lo, hi) = weighted_mean(vals, ws), weighted_bootstrap_ci(vals, ws, seed)
        else:
            mean, (lo, hi) = statistics.fmean(vals), bootstrap_ci(vals, seed)
        passes = [per_case_pass_all[c] for c in ids if c in per_case_pass_all]
        allp = [p for ps in passes for p in ps]
        out[sp] = {"n": len(ids), "mean": mean, "ci_low": lo, "ci_high": hi, "weighted": weighted,
                   "unweighted_mean": statistics.fmean(vals),
                   "pass_rate": (sum(allp) / len(allp)) if allp else None,
                   "pass_at_k": (sum(any(p) for p in passes) / len(passes)) if passes else None,
                   "pass_hat_k": (sum(all(p) for p in passes) / len(passes)) if passes else None}
    return out


def purpose_of(case: Case | None) -> str:
    """regression (default): the case should keep passing, and gates apply. capability: a target the system can't reach
    yet; reported separately, never gated, and graduated to regression once it passes reliably."""
    return "capability" if case is not None and getattr(case, "purpose", None) == "capability" else "regression"


def trial_drift(lst: list[Score], min_cases: int = 10, threshold: float = 0.15) -> dict[str, Any] | None:
    """Pass rate by trial (repeat) order. Independent trials should pass at about the same rate whatever their order;
    a drop or rise across repeats suggests trials share state (leftover data, caches, a sandbox not reset) or the
    environment degrades during the run (rate limits, resource exhaustion)."""
    by_rep: dict[int, list[bool]] = defaultdict(list)
    for s in lst:
        if not s.skipped and s.passed is not None:
            by_rep[s.repeat].append(bool(s.passed))
    reps = sorted(by_rep)
    if len(reps) < 2 or min(len(by_rep[r]) for r in reps) < min_cases:
        return None
    rates = {r: sum(by_rep[r]) / len(by_rep[r]) for r in reps}
    first, rest = rates[reps[0]], statistics.fmean(rates[r] for r in reps[1:])
    out: dict[str, Any] = {"pass_rate_by_repeat": rates, "spread": max(rates.values()) - min(rates.values())}
    if abs(first - rest) >= threshold:
        out["warning"] = (f"pass rate moves with trial order ({first:.0%} on the first trial, {rest:.0%} on later ones): trials "
                          "may share state (reset the environment per trial) or the environment degraded during the run")
    return out


def summarise(target: str, cases: list[Case], samples: list[Sample], scores: list[Score], seed: int = 7,
              on_error: str = "fail", stress_tags: list[str] | tuple[str, ...] = ("edge", "adversarial"),
              _capability: bool = False) -> dict[str, Any]:
    """Aggregate one target's scores.

    Each grader is also reported per split: `representative` (production-like cases, weighted by `case.weight` when
    given, so the mean estimates production performance) and `stress` (edge / adversarial cases, which find failures
    but would bias an average of production quality). Gate on them separately: mean(g, split="representative").

    Skipped scores (grader not applicable) are excluded. Errored scores (grader or target failed) count as a failure
    with value 0 when ``on_error="fail"`` (the default), so a grader that errors on hard cases cannot raise the mean;
    with ``on_error="exclude"`` they are left out and only counted.
    """
    if not _capability and any(purpose_of(c) == "capability" for c in cases):
        cap_ids = {c.id for c in cases if purpose_of(c) == "capability"}
        reg = summarise(target, [c for c in cases if c.id not in cap_ids], [s for s in samples if s.case_id not in cap_ids],
                        [s for s in scores if s.case_id not in cap_ids], seed, on_error, stress_tags, _capability=True)
        cap = summarise(target, [c for c in cases if c.id in cap_ids], [s for s in samples if s.case_id in cap_ids],
                        [s for s in scores if s.case_id in cap_ids], seed, on_error, stress_tags, _capability=True)
        reg["ops"] = summarise(target, cases, samples, [], seed, on_error, stress_tags, _capability=True)["ops"]
        reg["capability"] = {"cases": len(cap_ids), "graders": {g: {k: d.get(k) for k in
                                                                     ("mean", "ci_low", "ci_high", "pass_rate", "pass_hat_k",
                                                                      "n", "per_case_pass")}
                                                                 for g, d in cap["graders"].items()},
                             "ready_to_graduate": graduation_candidates(cap)}
        return reg
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
        unknown = sum(1 for s in lst if s.skipped and s.label == "unknown")
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
            "n": len(vals), "skipped": skipped, "unknown": unknown, "errors": errors,
            "per_case": case_means, "per_case_pass": case_pass,
            "judge_cost_usd": sum(s.cost_usd for s in lst),
            "mean_disagreement": (statistics.fmean([s.details["disagreement"] for s in lst if "disagreement" in s.details])
                                  if any("disagreement" in s.details for s in lst) else None),
        }
        graders[g]["splits"] = _split_stats(case_means, per_case_pass, by_case, stress_tags, seed)
        drift = trial_drift(lst)
        if drift:
            graders[g]["trial_drift"] = drift
        lw = [s.details["longer_won"] for s in lst if s.details.get("longer_won") is not None]
        if any("length_ratio" in s.details for s in lst):
            pc = [s.details["position_consistent"] for s in lst if "position_consistent" in s.details]
            graders[g]["length_bias"] = {
                "longer_win_rate": (sum(lw) / len(lw)) if lw else None, "decided": len(lw),
                "mean_length_ratio": statistics.fmean([s.details["length_ratio"] for s in lst if "length_ratio" in s.details]),
                "length_confounded": sum(1 for s in lst if s.details.get("length_confounded")),
                "position_consistency": (sum(pc) / len(pc)) if pc else None}
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
                    | {s.case_id for s in mine if s.case_id in crit_ids and s.error}
                    # a judge that could not decide is not a pass: critical cases need a decision
                    | {s.case_id for s in scores if s.target == target and s.case_id in crit_ids and s.label == "unknown"})
    split_n = {sp: sum(1 for c in cases if split_of(c, stress_tags) == sp) for sp in SPLITS}
    return {"target": target, "graders": graders, "ops": ops, "slices": dict(slices), "slice_n": dict(slice_n),
            "split_cases": split_n,
            "critical": {"total": len(crit_ids), "failed": failed, "passed": len(crit_ids) - len(failed)}}


def graduation_candidates(cap_summary: dict[str, Any]) -> list[str]:
    """Capability cases that passed every trial of every grader that scored them in this run."""
    per: dict[str, list[bool]] = defaultdict(list)
    for d in cap_summary["graders"].values():
        for cid, ok in (d.get("per_case_pass") or {}).items():
            per[cid].append(bool(ok))
    return sorted(c for c, oks in per.items() if oks and all(oks))


def metric_series(summary: dict[str, Any], samples: list[Sample], metric: str) -> list[float]:
    mine = [s for s in samples if s.target == summary["target"]]
    if metric in ("latency_ms", "ttft_ms", "cost_usd"):
        return [getattr(s, metric) for s in mine if getattr(s, metric) is not None]
    if metric == "tokens":
        return [float(s.usage.total) for s in mine]
    if metric == "steps":
        return [float(s.steps) for s in mine]
    raise KeyError(metric)
