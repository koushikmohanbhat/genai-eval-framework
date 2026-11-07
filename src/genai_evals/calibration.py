"""Judge calibration: agreement between a model grader and human labels (accuracy, Cohen's kappa, confusion matrix)."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_labels(path: str | Path) -> dict[tuple[str, str], bool]:
    """CSV columns: case_id, target (optional, '*' = any), label (pass/fail, 1/0, true/false, or a 0..1 score)."""
    out: dict[tuple[str, str], bool] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            raw = str(r["label"]).strip().lower()
            if raw in {"pass", "true", "yes", "1", "good"}:
                lab = True
            elif raw in {"fail", "false", "no", "0", "bad"}:
                lab = False
            else:
                lab = float(raw) >= 0.5
            out[(r["case_id"], r.get("target") or "*")] = lab
    return out


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float:
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def kappa_ci(pairs: list[tuple[bool, bool]], seed: int = 7, b: int = 2000, alpha: float = 0.05) -> tuple[float, float] | None:
    """Bootstrap 95% interval for Cohen's kappa (resampling labelled items)."""
    import random
    n = len(pairs)
    if n < 2:
        return None
    rng = random.Random(seed)
    ks = sorted(cohen_kappa([pairs[rng.randrange(n)] for _ in range(n)]) for _ in range(b))
    return ks[int(alpha / 2 * b)], ks[min(b - 1, int((1 - alpha / 2) * b))]


MIN_LABELS = 30


def calibrate(result: dict[str, Any], grader: str, labels: dict[tuple[str, str], bool]) -> dict[str, Any]:
    """Agreement between a model grader and human labels.

    Reports accuracy, Cohen's kappa with a bootstrap CI, per-class agreement (kappa alone is unstable when one class is
    rare), the human pass rate, and warnings for small or imbalanced label sets. Label a random sample of cases (or a
    stratified sample across tags), not hand-picked ones, so agreement generalises to the whole suite.
    """
    pairs, mismatches = [], []
    for s in result["scores"]:
        if s["grader"] != grader or s.get("skipped") or s.get("passed") is None:
            continue
        lab = labels.get((s["case_id"], s["target"]), labels.get((s["case_id"], "*")))
        if lab is None:
            continue
        pairs.append((bool(s["passed"]), lab))
        if bool(s["passed"]) != lab:
            mismatches.append({"case_id": s["case_id"], "target": s["target"], "judge": s["passed"], "human": lab})
    tp = sum(1 for j, h in pairs if j and h)
    tn = sum(1 for j, h in pairs if not j and not h)
    fp = sum(1 for j, h in pairs if j and not h)
    fn = sum(1 for j, h in pairs if not j and h)
    n = len(pairs)
    ci = kappa_ci(pairs)
    prevalence = (tp + fn) / n if n else None
    warnings = []
    if n < MIN_LABELS:
        warnings.append(f"only {n} labelled items (recommend at least {MIN_LABELS}); the kappa interval is wide")
    if prevalence is not None and (prevalence < 0.1 or prevalence > 0.9):
        warnings.append(f"human pass rate is {prevalence:.0%}: with one class this rare, kappa is unstable; "
                        "label more of the minority class")
    return {"grader": grader, "n": n, "accuracy": (tp + tn) / n if n else None, "kappa": cohen_kappa(pairs) if n else None,
            "kappa_ci": list(ci) if ci else None,
            "human_pass_rate": prevalence,
            "positive_agreement": (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None,
            "negative_agreement": (2 * tn / (2 * tn + fp + fn)) if (2 * tn + fp + fn) else None,
            "balanced_accuracy": ((tp / (tp + fn) if tp + fn else 0) + (tn / (tn + fp) if tn + fp else 0)) / 2 if n else None,
            "warnings": warnings,
            "confusion": {"judge_pass_human_pass": tp, "judge_fail_human_fail": tn, "judge_pass_human_fail": fp, "judge_fail_human_pass": fn},
            "mismatches": mismatches[:50], "calibrated_at": datetime.now(timezone.utc).isoformat()}


def load_calibration(path: str | Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {d["grader"]: d for d in (data if isinstance(data, list) else data.get("graders", [data]))}


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
    return out
