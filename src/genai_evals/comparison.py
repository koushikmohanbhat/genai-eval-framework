"""Side-by-side comparison of a candidate against a baseline on the same cases.

Used by experiments (one run, several variants), `genai-evals compare` (two runs) and `prompts diff`. For each
candidate it reports, against the baseline:

- quality: per-grader mean, pass^k, paired delta with a bootstrap CI, McNemar p-value, and which cases newly fail or
  were fixed
- cost and latency: means, p95, relative change and a paired delta per case
- critical cases newly failing or fixed
- slices (tags) ranked by how much they moved; slices under ``min_n`` cases are listed but not ranked
- failure types (graders) whose failures appeared or disappeared, and the cases that flipped
- a verdict: ``regresses``, ``trade-off``, ``improves`` or ``no detectable difference``
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from .stats import paired_delta

OPS_METRICS = ("cost_usd", "latency_ms")


def _per_case(samples: list[dict[str, Any]] | None, target: str, metric: str) -> dict[str, float]:
    vals: dict[str, list[float]] = defaultdict(list)
    for s in samples or []:
        if s.get("target") == target and s.get(metric) is not None:
            vals[s["case_id"]].append(float(s[metric]))
    return {c: statistics.fmean(v) for c, v in vals.items()}


def _rel(cand: float | None, base: float | None) -> float | None:
    if cand is None or not base:
        return None
    return cand / base - 1


def compare_pair(base: dict[str, Any], cand: dict[str, Any], *, cases: list[dict[str, Any]],
                 base_samples: list[dict[str, Any]] | None = None, cand_samples: list[dict[str, Any]] | None = None,
                 seed: int = 7, alpha: float = 0.05, min_n: int = 5, graders: list[str] | None = None,
                 top_slices: int = 10) -> dict[str, Any]:
    """Compare two target summaries (as produced by ``stats.summarise``) case by case."""
    tags = {c["id"]: c.get("tags", []) for c in cases}

    quality: dict[str, Any] = {}
    for g, b in base.get("graders", {}).items():
        c = cand.get("graders", {}).get(g)
        if (graders and g not in graders) or not c:
            continue
        d = paired_delta(c["per_case"], b["per_case"], seed, c.get("per_case_pass"), b.get("per_case_pass"), alpha=alpha)
        bp, cp = b.get("per_case_pass", {}), c.get("per_case_pass", {})
        common = sorted(set(bp) & set(cp))
        quality[g] = {
            "baseline": b.get("mean"), "candidate": c.get("mean"),
            "baseline_pass_hat_k": b.get("pass_hat_k"), "candidate_pass_hat_k": c.get("pass_hat_k"),
            "delta": d["delta"], "ci_low": d["ci_low"], "ci_high": d["ci_high"], "significant": d["significant"],
            "mcnemar_p": d.get("mcnemar_p"), "n": d["n"], "warning": d.get("warning"),
            "new_failures": [x for x in common if bp[x] and not cp[x]],
            "fixed": [x for x in common if not bp[x] and cp[x]],
        }

    ops: dict[str, Any] = {}
    for m in OPS_METRICS:
        bo, co = base.get("ops", {}).get(m, {}), cand.get("ops", {}).get(m, {})
        bpc, cpc = _per_case(base_samples, base["target"], m), _per_case(cand_samples, cand["target"], m)
        d = paired_delta(cpc, bpc, seed, alpha=alpha) if bpc and cpc else None
        ops[m] = {"baseline_mean": bo.get("mean"), "candidate_mean": co.get("mean"),
                  "baseline_p95": bo.get("p95"), "candidate_p95": co.get("p95"),
                  "relative": _rel(co.get("mean"), bo.get("mean")),
                  "delta": d["delta"] if d and d["n"] else None, "ci_low": d["ci_low"] if d and d["n"] else None,
                  "ci_high": d["ci_high"] if d and d["n"] else None, "significant": bool(d and d["n"] and d["significant"])}
    ops["errors"] = {"baseline": base.get("ops", {}).get("errors"), "candidate": cand.get("ops", {}).get("errors")}

    bf, cf = set(base.get("critical", {}).get("failed", [])), set(cand.get("critical", {}).get("failed", []))
    critical = {"total": cand.get("critical", {}).get("total", 0), "baseline_failed": sorted(bf), "candidate_failed": sorted(cf),
                "new_failures": sorted(cf - bf), "fixed": sorted(bf - cf)}

    rows, small = [], []
    all_tags = sorted({t for ts in tags.values() for t in ts})
    for g in quality:
        bpc, cpc = base["graders"][g]["per_case"], cand["graders"][g]["per_case"]
        for t in all_tags:
            ids = [c for c in tags if t in tags[c] and c in bpc and c in cpc]
            if not ids:
                continue
            bsub, csub = {c: bpc[c] for c in ids}, {c: cpc[c] for c in ids}
            row = {"slice": t, "grader": g, "n": len(ids), "baseline": statistics.fmean(bsub.values()),
                   "candidate": statistics.fmean(csub.values())}
            row["delta"] = row["candidate"] - row["baseline"]
            if abs(row["delta"]) < 1e-9:
                continue
            if len(ids) >= min_n:
                d = paired_delta(csub, bsub, seed, alpha=alpha)
                row.update(ci_low=d["ci_low"], ci_high=d["ci_high"], significant=d["significant"])
                rows.append(row)
            else:
                small.append(row)
    rows.sort(key=lambda r: -abs(r["delta"]))
    small.sort(key=lambda r: -abs(r["delta"]))

    failure_types = sorted(({"grader": g, "new_failures": len(q["new_failures"]), "fixed": len(q["fixed"])}
                            for g, q in quality.items() if q["new_failures"] or q["fixed"]),
                           key=lambda r: (-r["new_failures"], -r["fixed"], r["grader"]))
    flips: dict[str, list[dict[str, str]]] = defaultdict(list)
    for g, q in quality.items():
        for c in q["new_failures"]:
            flips[c].append({"grader": g, "change": "new failure"})
        for c in q["fixed"]:
            flips[c].append({"grader": g, "change": "fixed"})
    flipped = [{"case_id": c, "tags": tags.get(c, []), "critical": c in critical["new_failures"] or c in critical["fixed"],
                "changes": v} for c, v in sorted(flips.items())]

    regressions = sorted(g for g, q in quality.items() if q["significant"] and q["delta"] < 0)
    improvements = sorted(g for g, q in quality.items() if q["significant"] and q["delta"] > 0)
    if critical["new_failures"] or (regressions and not improvements):
        verdict = "regresses"
    elif regressions and improvements:
        verdict = "trade-off"
    elif improvements:
        verdict = "improves"
    else:
        verdict = "no detectable difference"

    return {"baseline": base["target"], "candidate": cand["target"], "verdict": verdict,
            "regressions": regressions, "improvements": improvements, "quality": quality, "ops": ops,
            "critical": critical, "slices": rows[:top_slices], "slices_below_min_n": small[:top_slices],
            "failure_types": failure_types, "flipped_cases": flipped, "alpha": alpha, "min_n": min_n}


def n_tests(n_candidates: int, n_graders: int) -> int:
    """Hypothesis tests that drive verdicts: one per candidate x grader, plus cost and latency per candidate."""
    return max(1, n_candidates * (n_graders + len(OPS_METRICS)))


def compare_runs(cur: dict[str, Any], base: dict[str, Any], mapping: dict[str, str] | None = None, alpha: float = 0.05,
                 correction: str = "bonferroni", min_n: int = 5) -> dict[str, Any]:
    """Compare every target in ``cur`` with the same-named (or mapped) target in a baseline run. Baselines saved with
    ``baseline promote`` carry no samples, so cost / latency then show means only."""
    bs = base.get("summary", {})
    pairs = [(t, (mapping or {}).get(t, t)) for t in cur["summary"] if (mapping or {}).get(t, t) in bs]
    graders = {g for t, _ in pairs for g in cur["summary"][t]["graders"]}
    m = n_tests(len(pairs), len(graders)) if correction == "bonferroni" else 1
    seed = int(cur.get("seed", 7) or 7)
    results = {t: compare_pair(bs[b], cur["summary"][t], cases=cur.get("cases", []), base_samples=base.get("samples"),
                               cand_samples=cur.get("samples"), seed=seed, alpha=alpha / m, min_n=min_n)
               for t, b in pairs}
    return {"name": f"{cur['suite']}: {cur['run_id']} vs {base.get('run_id', 'baseline')}", "description": "",
            "hypothesis": "", "baseline": base.get("run_id", "baseline"),
            "variants": [{"name": t, "description": f"run {cur['run_id']} target {t} vs {b}", "changes": {}} for t, b in pairs],
            "alpha": alpha, "correction": correction, "tests": m, "alpha_per_test": alpha / m,
            "n_cases": len(cur.get("cases", [])), "repeats": cur.get("repeats"), "results": results, "gates": {}, "dropped_gates": []}
