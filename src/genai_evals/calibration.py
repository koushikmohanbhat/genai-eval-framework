"""Judge calibration: agreement between a model grader and human labels (accuracy, Cohen's kappa, confusion matrix).

Human labels can come from one reviewer or several. With several (a `reviewer` column), the labels are first combined
into a consensus (majority, unanimous, or an adjudicator's call on disputed items), and agreement *between the
reviewers* is reported alongside: a judge cannot be expected to agree with humans more than they agree with each other.

Label samples are usually *designed*, not random: `labels sheet` stratifies by tag and balances judge passes and fails,
so agreement can be measured on both outcomes and on rare slices. That sample's pass rate and overall agreement do not
reflect production prevalence. So the sheet records each item's design and inverse-inclusion weight, and calibration
reports (a) agreement on the calibration sample, (b) a production-weighted estimate from those weights, (c) agreement
per slice and per split, and (d) agreement on a separate representative validation sample when one is given.

`compare_judges` supports the judge ladder from OpenAI's evaluation best practices: start with a strong judge, check it
against human labels, and move to a cheaper judge only when it agrees with the humans about as well (non-inferiority on
kappa, on the same labelled items).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_label(raw: Any) -> bool | None:
    raw = str(raw).strip().lower()
    if raw in {"pass", "true", "yes", "1", "good"}:
        return True
    if raw in {"fail", "false", "no", "0", "bad"}:
        return False
    if raw in {"", "skip", "unsure", "n/a", "na"}:
        return None
    return float(raw) >= 0.5


def read_reviews(path: str | Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """CSV columns: case_id, target (optional, '*' = any), label, and optionally reviewer, round and adjudicated
    (true on the row that settles a disputed item). Blank / 'unsure' labels are ignored. Several rows per item are
    kept; a later round from the same reviewer replaces their earlier one."""
    out: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            lab = parse_label(r.get("label", ""))
            if lab is None:
                continue
            key = (r["case_id"], r.get("target") or "*")
            who = (r.get("reviewer") or "").strip() or f"_row{i}"
            rnd = int(r.get("round") or 1)
            adj = str(r.get("adjudicated") or "").strip().lower() in {"true", "yes", "1"}
            prev = out.setdefault(key, {}).get(who)
            if prev is None or rnd >= prev["round"]:
                out[key][who] = {"reviewer": who, "label": lab, "round": rnd, "adjudicated": adj}
    return {k: list(v.values()) for k, v in out.items()}


def consensus(reviews: dict[tuple[str, str], list[dict[str, Any]]], rule: str = "majority"
              ) -> tuple[dict[tuple[str, str], bool], list[dict[str, Any]]]:
    """Combine reviews into one label per item. rule: majority (ties are disputed), unanimous (any disagreement is
    disputed). An adjudicated row always decides. Returns (labels, disputed items)."""
    labels, disputed = {}, []
    for key, rows in reviews.items():
        adj = [r for r in rows if r["adjudicated"]]
        if adj:
            labels[key] = adj[-1]["label"]
            continue
        votes = [r["label"] for r in rows]
        yes = sum(votes)
        if rule == "unanimous":
            if yes in (0, len(votes)):
                labels[key] = bool(yes)
            else:
                disputed.append({"case_id": key[0], "target": key[1], "pass": yes, "fail": len(votes) - yes})
        elif yes * 2 == len(votes):
            disputed.append({"case_id": key[0], "target": key[1], "pass": yes, "fail": len(votes) - yes})
        else:
            labels[key] = yes * 2 > len(votes)
    return labels, disputed


def fleiss_kappa(items: list[list[bool]]) -> float | None:
    """Fleiss' kappa for binary labels; items with fewer than two ratings are skipped."""
    items = [it for it in items if len(it) >= 2]
    if not items:
        return None
    p_bar, tot_yes, tot = 0.0, 0, 0
    for it in items:
        n, y = len(it), sum(it)
        p_bar += (y * (y - 1) + (n - y) * (n - y - 1)) / (n * (n - 1))
        tot_yes, tot = tot_yes + y, tot + n
    p_bar /= len(items)
    p = tot_yes / tot
    pe = p * p + (1 - p) * (1 - p)
    return 1.0 if pe == 1 else (p_bar - pe) / (1 - pe)


def reviewer_agreement(reviews: dict[tuple[str, str], list[dict[str, Any]]], rule: str = "majority") -> dict[str, Any]:
    """Agreement between human reviewers: raw agreement on multiply-reviewed items, Fleiss' kappa, and each reviewer's
    agreement with the consensus (excluding their own vote would need 3+ reviewers, so it is included)."""
    multi = {k: v for k, v in reviews.items() if len([r for r in v if not r["adjudicated"]]) >= 2}
    labels, disputed = consensus(reviews, rule)
    per: dict[str, list[bool]] = {}
    for k, rows in reviews.items():
        if k not in labels:
            continue
        for r in rows:
            if not r["adjudicated"] and not r["reviewer"].startswith("_row"):
                per.setdefault(r["reviewer"], []).append(r["label"] == labels[k])
    votes = [[r["label"] for r in v if not r["adjudicated"]] for v in multi.values()]
    raw = [len(set(v)) == 1 for v in votes]
    fk = fleiss_kappa(votes)
    warnings = []
    if not multi:
        warnings.append("no item has two or more reviewers: agreement between reviewers cannot be measured")
    elif fk is not None and fk < 0.6:
        warnings.append(f"reviewers agree only moderately (Fleiss' kappa {fk:.2f}): tighten the labelling guide "
                        "(examples at each level) before trusting any judge against these labels")
    if disputed:
        warnings.append(f"{len(disputed)} items are disputed and left out until adjudicated")
    return {"items": len(reviews), "multi_reviewed": len(multi), "raw_agreement": (sum(raw) / len(raw)) if raw else None,
            "fleiss_kappa": fk, "reviewers": {r: {"n": len(v), "agreement_with_consensus": sum(v) / len(v)}
                                              for r, v in sorted(per.items())},
            "rule": rule, "disputed": disputed[:100], "labelled": len(labels), "warnings": warnings}


def read_sample_info(path: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Per labelled item: the sample design (stratified | random), stratum and inverse-inclusion weight, when the labels
    file came from `labels sheet` (columns sample_design, stratum, weight). Items without them get nothing."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["case_id"], r.get("target") or "*")
            info = {}
            if str(r.get("repeat") or "").strip().isdigit():
                info["repeat"] = int(r["repeat"])
            if (r.get("sample_design") or "").strip():
                info["design"] = r["sample_design"].strip()
            if (r.get("stratum") or "").strip():
                info["stratum"] = r["stratum"].strip()
            try:
                if (r.get("weight") or "").strip():
                    info["weight"] = float(r["weight"])
            except ValueError:
                pass
            if info:
                out.setdefault(key, info)
    return out


def read_labels(path: str | Path, rule: str = "majority") -> dict[tuple[str, str], bool]:
    """Labels per (case_id, target). With several reviewers per item, returns their consensus (see `consensus`)."""
    return consensus(read_reviews(path), rule)[0]


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float:
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def _resample(n: int, strata: list[Any] | None, rng) -> list[int]:
    """Bootstrap indices. With strata, resample within each stratum (keeping its size), which respects a stratified
    sampling design; without, resample all units together."""
    if not strata:
        return [rng.randrange(n) for _ in range(n)]
    by: dict[Any, list[int]] = {}
    for i, st in enumerate(strata):
        by.setdefault(st, []).append(i)
    out: list[int] = []
    for idx in by.values():
        out += [idx[rng.randrange(len(idx))] for _ in idx]
    return out


def kappa_ci(pairs: list[tuple[bool, bool]], seed: int = 7, b: int = 2000, alpha: float = 0.05,
             strata: list[Any] | None = None) -> tuple[float, float] | None:
    """Bootstrap 95% interval for Cohen's kappa, resampling labelled units (within strata when given)."""
    import random
    n = len(pairs)
    if n < 2:
        return None
    rng = random.Random(seed)
    ks = sorted(k for k in (cohen_kappa([pairs[i] for i in _resample(n, strata, rng)]) for _ in range(b)) if k == k)
    if not ks:
        return None
    return ks[int(alpha / 2 * len(ks))], ks[min(len(ks) - 1, int((1 - alpha / 2) * len(ks)))]


def weighted_kappa(pairs: list[tuple[bool, bool]], weights: list[float]) -> float:
    """Cohen's kappa with item weights (each item counts as its inverse-inclusion weight), estimating agreement at
    the population's prevalence rather than the sample's."""
    w = sum(weights)
    if not pairs or w <= 0:
        return float("nan")
    po = sum(wi for (a, b), wi in zip(pairs, weights) if a == b) / w
    pa = sum(wi for (a, _), wi in zip(pairs, weights) if a) / w
    pb = sum(wi for (_, b), wi in zip(pairs, weights) if b) / w
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def _weighted_ci(pairs, weights, seed: int = 7, b: int = 2000, strata: list[Any] | None = None) -> tuple[float, float] | None:
    """Design-aware interval for the weighted kappa: a stratified bootstrap that resamples units within each sampling
    stratum (keeping the stratum's size and weight), so the interval reflects how the sample was drawn."""
    import random
    n = len(pairs)
    if n < 2:
        return None
    rng = random.Random(seed)
    ks = []
    for _ in range(b):
        idx = _resample(n, strata, rng)
        k = weighted_kappa([pairs[i] for i in idx], [weights[i] for i in idx])
        if k == k:
            ks.append(k)
    ks.sort()
    return (ks[int(0.025 * len(ks))], ks[min(len(ks) - 1, int(0.975 * len(ks)))]) if ks else None


def _agreement(pairs: list[tuple[bool, bool]], seed: int = 7) -> dict[str, Any]:
    n = len(pairs)
    if not n:
        return {"n": 0}
    k = cohen_kappa(pairs)
    ci = kappa_ci(pairs, seed)
    return {"n": n, "accuracy": sum(a == b for a, b in pairs) / n, "kappa": k if k == k else None,
            "kappa_ci": list(ci) if ci else None, "human_pass_rate": sum(h for _, h in pairs) / n,
            "judge_pass_rate": sum(j for j, _ in pairs) / n}


MIN_LABELS = 30


def _pairs(result: dict[str, Any], grader: str, labels: dict[tuple[str, str], bool],
           info: dict[tuple[str, str], dict[str, Any]] | None = None, stats: dict[str, int] | None = None
           ) -> dict[tuple[str, str], tuple[bool, bool]]:
    """One (judge, human) pair per labelled **unit (case, target)**. The judge verdict is the one on the repeat that
    was labelled (the sheet's `repeat` column). Without that column, repeat 0 is used, and `stats` counts the
    units where this was a guess because the run had several repeats. Labels never fan out across repeats."""
    info = info or {}
    by_unit: dict[tuple[str, str], dict[int, bool]] = {}
    for s in result["scores"]:
        if s["grader"] != grader or s.get("skipped") or s.get("passed") is None:
            continue
        by_unit.setdefault((s["case_id"], s["target"]), {})[int(s.get("repeat") or 0)] = bool(s["passed"])
    out = {}
    for (cid, tgt), reps in by_unit.items():
        key = (cid, tgt) if (cid, tgt) in labels else (cid, "*")
        lab = labels.get(key)
        if lab is None:
            continue
        rep = (info.get((cid, tgt)) or info.get((cid, "*")) or {}).get("repeat")
        if rep is None:
            rep = min(reps)
            if stats is not None and len(reps) > 1:
                stats["repeat_unspecified"] = stats.get("repeat_unspecified", 0) + 1
        if rep not in reps:
            if stats is not None:
                stats["repeat_missing"] = stats.get("repeat_missing", 0) + 1
            continue
        out[(cid, tgt)] = (reps[rep], lab)
    return out


def _ladder_sample(pc: dict, ps: dict, info: dict[tuple[str, str], dict[str, Any]] | None, seed: int, b: int
                   ) -> dict[str, Any]:
    """Kappa of two judges against the same human labels on one labelled sample, and a design-aware 95% interval for
    their difference: units are resampled within the sample's strata (when recorded), and when every unit has an
    inverse-inclusion weight the kappas are weighted, so the difference estimates production rather than the sample."""
    import random
    info = info or {}

    def info_for(k):
        return info.get(k) or info.get((k[0], "*")) or {}
    common = sorted(set(pc) & set(ps))
    a, s_ = [pc[k] for k in common], [ps[k] for k in common]
    n = len(common)
    strata = [info_for(k).get("stratum") for k in common]
    strata = strata if n and all(x is not None for x in strata) else None
    weights = [info_for(k).get("weight") for k in common]
    weights = [float(w) for w in weights] if n and all(w is not None for w in weights) else None
    designs = sorted({info_for(k).get("design") for k in common if info_for(k).get("design")})
    design = designs[0] if len(designs) == 1 else ("mixed" if designs else "unknown")
    out: dict[str, Any] = {"n": n, "design": design, "stratified_interval": strata is not None, "weighted": weights is not None}
    if n < 2:
        return out

    def kap(prs, idx=None):
        idx = idx if idx is not None else range(n)
        if weights:
            return weighted_kappa([prs[i] for i in idx], [weights[i] for i in idx])
        return cohen_kappa([prs[i] for i in idx])
    kc, ks = kap(a), kap(s_)
    rng = random.Random(seed)
    diffs, cks = [], []
    for _ in range(b):
        idx = _resample(n, strata, rng)
        c, st = kap(a, idx), kap(s_, idx)
        if c == c:
            cks.append(c)
        if c == c and st == st:
            diffs.append(c - st)
    diffs.sort()
    cks.sort()

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    tot = sum(weights) if weights else n
    w = weights or [1.0] * n
    return out | {"kappa_cheap": kc, "kappa_strong": ks, "kappa_diff": kc - ks,
                  "kappa_diff_ci": [q(diffs, 0.025), q(diffs, 0.975)] if diffs else None,
                  "kappa_cheap_ci": [q(cks, 0.025), q(cks, 0.975)] if cks else None,
                  "accuracy_cheap": sum(wi for (j, h), wi in zip(a, w) if j == h) / tot,
                  "accuracy_strong": sum(wi for (j, h), wi in zip(s_, w) if j == h) / tot}


def compare_judges(cheap: tuple[dict[str, Any], str], strong: tuple[dict[str, Any], str],
                   labels: dict[tuple[str, str], bool], margin: float = 0.05, min_kappa: float | None = None,
                   min_n: int = MIN_LABELS, seed: int = 7, b: int = 2000,
                   info: dict[tuple[str, str], dict[str, Any]] | None = None,
                   validation: dict[tuple[str, str], bool] | None = None,
                   validation_info: dict[tuple[str, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Can a cheaper judge replace a stronger one? Both are scored against the same human labels on the units both
    graded. The cheaper judge is approved when (a) the 95% lower bound of kappa(cheap) - kappa(strong) is above
    -margin (it is not meaningfully worse), (b) at least min_n units are shared, and (c) if min_kappa is given, its own
    kappa lower bound clears it.

    The comparison respects how the labels were drawn. On the calibration sample, the interval resamples within the
    sheet's strata and, when units carry inverse-inclusion weights, compares production-weighted kappas. When a separate
    random validation sample is given, **approval is decided on it** (it is the unbiased estimate of production
    agreement) and the calibration sample is reported alongside."""
    pc, ps = _pairs(cheap[0], cheap[1], labels, info), _pairs(strong[0], strong[1], labels, info)
    cal_s = _ladder_sample(pc, ps, info, seed, b)
    val_s = None
    if validation:
        vc = _pairs(cheap[0], cheap[1], validation, validation_info)
        vs = _pairs(strong[0], strong[1], validation, validation_info)
        val_s = _ladder_sample(vc, vs, validation_info, seed, b)
    if val_s is not None:
        basis, used = "validation sample", val_s
        if val_s["design"] not in ("random", "unknown") and not val_s["weighted"]:
            basis += f" (design {val_s['design']}, unweighted: may not estimate production)"
    elif cal_s["design"] == "stratified" and cal_s["weighted"]:
        basis, used = "calibration sample, production-weighted (stratified bootstrap)", cal_s
    elif cal_s["design"] == "stratified":
        basis, used = "calibration sample, stratified but unweighted: estimates the sample, not production", cal_s
    else:
        basis, used = f"calibration sample (design {cal_s['design']})", cal_s
    out: dict[str, Any] = {"cheap": cheap[1], "strong": strong[1], "n": used["n"], "margin": margin, "basis": basis,
                           "calibration_sample": cal_s, "validation_sample": val_s}
    if used["n"] < 2:
        return out | {"approved": False, "reason": f"only {used['n']} units graded by both judges and labelled ({basis})"}
    reasons = []
    lo, hi = used["kappa_diff_ci"] or (float("-inf"), float("inf"))
    cci = used["kappa_cheap_ci"]
    if used["n"] < min_n:
        reasons.append(f"only {used['n']} shared labelled units in the {basis.split(' (')[0].split(',')[0]} (min {min_n})")
    if lo < -margin:
        reasons.append(f"cheaper judge may be worse: kappa difference 95% CI [{lo:.2f}, {hi:.2f}] goes below -{margin}")
    if min_kappa is not None and cci and cci[0] < min_kappa:
        reasons.append(f"cheaper judge's kappa lower bound {cci[0]:.2f} < {min_kappa}")
    if "unweighted" in basis and not reasons:
        reasons.append("the labelled sample is stratified without weights, so agreement may not hold in production: "
                       "label a random sample and pass it as --validation-labels")
    if val_s is not None and cal_s.get("kappa_diff_ci") and cal_s["kappa_diff_ci"][0] < -margin and not reasons:
        out["note"] = ("on the calibration sample (over-weighting hard cases) the cheaper judge may be worse "
                       f"(difference CI lower bound {cal_s['kappa_diff_ci'][0]:.2f}): check the slices it misses")
    return out | {k: used[k] for k in ("kappa_cheap", "kappa_strong", "kappa_cheap_ci", "kappa_diff", "kappa_diff_ci",
                                         "accuracy_cheap", "accuracy_strong")} | {
                  "judge_cost_cheap": _judge_cost(cheap[0], cheap[1]), "judge_cost_strong": _judge_cost(strong[0], strong[1]),
                  "approved": not reasons,
                  "reason": "; ".join(reasons) or f"not meaningfully worse than the stronger judge ({basis})"}


def _judge_cost(result: dict[str, Any], grader: str) -> float:
    return sum(s.get("cost_usd") or 0.0 for s in result["scores"] if s["grader"] == grader)


def calibrate(result: dict[str, Any], grader: str, labels: dict[tuple[str, str], bool],
              human: dict[str, Any] | None = None, sample_info: dict[tuple[str, str], dict[str, Any]] | None = None,
              validation: dict[tuple[str, str], bool] | None = None,
              validation_info: dict[tuple[str, str], dict[str, Any]] | None = None,
              stress_tags: list[str] | tuple[str, ...] = ("edge", "adversarial")) -> dict[str, Any]:
    """Agreement between a model grader and human labels.

    Reports accuracy, Cohen's kappa with a bootstrap CI, per-class agreement (kappa alone is unstable when one class is
    rare), the human pass rate, and warnings for small or imbalanced label sets. Label a random sample of cases (or a
    stratified sample across tags), not hand-picked ones, so agreement generalises to the whole suite.
    """
    pairs, mismatches, keys = [], [], []
    pstats: dict[str, int] = {}
    for (cid, tgt), (j, lab) in _pairs(result, grader, labels, sample_info, pstats).items():
        pairs.append((j, lab))
        keys.append((cid, tgt))
        if j != lab:
            mismatches.append({"case_id": cid, "target": tgt, "judge": j, "human": lab})
    tp = sum(1 for j, h in pairs if j and h)
    tn = sum(1 for j, h in pairs if not j and not h)
    fp = sum(1 for j, h in pairs if j and not h)
    fn = sum(1 for j, h in pairs if not j and h)
    n = len(pairs)
    info = sample_info or {}

    def info_for(k):
        return info.get(k) or info.get((k[0], "*")) or {}
    strata = [info_for(k).get("stratum") for k in keys]
    strata = strata if all(x is not None for x in strata) else None
    ci = kappa_ci(pairs, strata=strata)
    prevalence = (tp + fn) / n if n else None
    warnings = []
    if n < MIN_LABELS:
        warnings.append(f"only {n} labelled items (recommend at least {MIN_LABELS}); the kappa interval is wide")
    if prevalence is not None and (prevalence < 0.1 or prevalence > 0.9):
        warnings.append(f"human pass rate is {prevalence:.0%}: with one class this rare, kappa is unstable; "
                        "label more of the minority class")
    if human:
        warnings += [w for w in human.get("warnings", []) if "no item has two" not in w]
        hk = human.get("fleiss_kappa")
        jk = cohen_kappa(pairs) if n else None
        if hk is not None and jk is not None and jk > hk + 0.1:
            warnings.append(f"judge agrees with the consensus (kappa {jk:.2f}) more than reviewers agree with each other "
                            f"({hk:.2f}): check the labels are independent")
    # --- how the labelled sample was drawn, and what it says about production --------------------------------------
    if pstats.get("repeat_unspecified"):
        warnings.append(f"{pstats['repeat_unspecified']} labelled unit(s) had several repeats but no `repeat` column: "
                        "compared with repeat 0 only (use `labels sheet`, which records the labelled repeat)")
    if pstats.get("repeat_missing"):
        warnings.append(f"{pstats['repeat_missing']} label(s) name a repeat this result doesn't have: left out")
    designs = sorted({info_for(k).get("design") for k in keys if info_for(k).get("design")})
    design = designs[0] if len(designs) == 1 else ("mixed" if designs else "unknown")
    weights = [info_for(k).get("weight") for k in keys]
    weighted = None
    if n and all(w is not None for w in weights):
        wk = weighted_kappa(pairs, weights)
        wci = _weighted_ci(pairs, weights, strata=strata)
        tot = sum(weights)
        weighted = {"kappa": wk if wk == wk else None, "kappa_ci": list(wci) if wci else None,
                    "accuracy": sum(w for (a, b), w in zip(pairs, weights) if a == b) / tot,
                    "human_pass_rate": sum(w for (_, h), w in zip(pairs, weights) if h) / tot,
                    "judge_pass_rate": sum(w for (j, _), w in zip(pairs, weights) if j) / tot}
    # --- agreement per slice (tag) and per split --------------------------------------------------------------------
    case_tags = {c["id"]: c.get("tags") or [] for c in result.get("cases", [])}
    case_split = {c["id"]: (c.get("split") if c.get("split") in ("representative", "stress")
                            else ("stress" if set(c.get("tags") or []) & set(stress_tags) else "representative"))
                  for c in result.get("cases", [])}
    groups: dict[str, list[tuple[bool, bool]]] = {}
    for (cid, _t), pr in zip(keys, pairs):
        for t in case_tags.get(cid, []):
            groups.setdefault(t, []).append(pr)
        if cid in case_split:
            groups.setdefault(f"split:{case_split[cid]}", []).append(pr)
    slices = {g: _agreement(prs) for g, prs in sorted(groups.items())}
    overall_acc = (tp + tn) / n if n else None
    weak = [g for g, d in slices.items() if d["n"] >= 5 and overall_acc is not None and d["accuracy"] < overall_acc - 0.15]
    if weak:
        warnings.append("judge agrees noticeably less on: " + ", ".join(f"{g} ({slices[g]['accuracy']:.0%}, n={slices[g]['n']})" for g in weak))
    # --- a separate representative validation sample ----------------------------------------------------------------
    val = None
    if validation:
        vp = list(_pairs(result, grader, validation, validation_info).values())
        val = _agreement(vp)
    if design == "stratified":
        msg = ("calibration sample is stratified (balanced on the judge's verdict and spread across tags), so its pass "
               f"rate ({prevalence:.0%}) and overall agreement are not production estimates" if prevalence is not None else "")
        if weighted:
            msg += f"; production-weighted: human pass rate {weighted['human_pass_rate']:.0%}, kappa {weighted['kappa']:.2f}"
        if not val:
            msg += "; label a random sample too (`labels sheet --design random`) and pass it as --validation-labels"
        if msg:
            warnings.append(msg)
    elif design == "unknown" and not val:
        warnings.append("sampling design unknown (labels not from `labels sheet`): if the items were hand-picked, "
                        "agreement may not generalise; label a random sample as --validation-labels")
    if val and val.get("kappa_ci") and ci and val["kappa_ci"][0] < ci[0] - 0.1:
        warnings.append(f"agreement on the representative validation sample is lower (kappa {val['kappa']:.2f}) than on "
                        f"the calibration sample ({cohen_kappa(pairs):.2f})")
    return {"grader": grader, "n": n, "unit": "case, target", "accuracy": (tp + tn) / n if n else None,
            "kappa": cohen_kappa(pairs) if n else None,
            "sample": {"design": design, "weighted": weighted is not None, "stratified_interval": strata is not None}, "weighted": weighted,
            "slices": slices, "validation": val,
            "kappa_ci": list(ci) if ci else None,
            "human_pass_rate": prevalence,
            "positive_agreement": (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None,
            "negative_agreement": (2 * tn / (2 * tn + fp + fn)) if (2 * tn + fp + fn) else None,
            "balanced_accuracy": ((tp / (tp + fn) if tp + fn else 0) + (tn / (tn + fp) if tn + fp else 0)) / 2 if n else None,
            "warnings": warnings,
            "confusion": {"judge_pass_human_pass": tp, "judge_fail_human_fail": tn, "judge_pass_human_fail": fp, "judge_fail_human_pass": fn},
            "mismatches": mismatches[:50], "human_agreement": human,
            "calibrated_at": datetime.now(timezone.utc).isoformat()}


def load_calibration(path: str | Path | None) -> dict[str, Any]:
    """Calibrations keyed by grader. Accepts what `genai-evals calibrate` writes ({"graders": [...]}), a list of
    entries, one entry, or a mapping of grader name to entry."""
    if not path or not Path(path).exists():
        return {}
    return normalise_calibration(json.loads(Path(path).read_text(encoding="utf-8")))


def normalise_calibration(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {d["grader"]: d for d in data}
    if isinstance(data, dict) and "graders" in data:
        return {d["grader"]: d for d in data["graders"]}
    if isinstance(data, dict) and "grader" in data:
        return {data["grader"]: data}
    if isinstance(data, dict):
        return {g: {"grader": g, **d} for g, d in data.items() if isinstance(d, dict)}
    return {}


def untrusted_graders(calibration: dict[str, Any], min_kappa: float | None, min_n: int = MIN_LABELS) -> dict[str, str]:
    """A judge is trusted only if enough items were labelled and the *lower bound* of its kappa interval clears
    min_kappa. A point estimate alone overstates reliability on small label sets."""
    if min_kappa is None:
        return {}
    out = {}
    for g, d in calibration.items():
        if d.get("kappa") is None:
            continue
        if (d.get("n") or 0) < min_n:
            out[g] = f"only {d.get('n')} human labels (min {min_n})"
            continue
        ci = d.get("kappa_ci")
        low = ci[0] if ci else d["kappa"]
        if low < min_kappa:
            out[g] = (f"kappa {d['kappa']:.2f}, 95% CI lower bound {low:.2f} < {min_kappa}" if ci
                      else f"kappa {d['kappa']:.2f} < {min_kappa}")
            continue
        # when a representative validation sample was labelled, the judge must also clear the bar there
        v = d.get("validation") or {}
        if v.get("kappa") is not None:
            vlow = (v.get("kappa_ci") or [v["kappa"]])[0]
            if vlow < min_kappa:
                out[g] = f"on the representative validation sample: kappa {v['kappa']:.2f}, 95% CI lower bound {vlow:.2f} < {min_kappa}"
    return out
