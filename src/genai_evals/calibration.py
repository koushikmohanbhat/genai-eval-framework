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


def calibrate(result: dict[str, Any], grader: str, labels: dict[tuple[str, str], bool]) -> dict[str, Any]:
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
    return {"grader": grader, "n": n, "accuracy": (tp + tn) / n if n else None, "kappa": cohen_kappa(pairs) if n else None,
            "confusion": {"judge_pass_human_pass": tp, "judge_fail_human_fail": tn, "judge_pass_human_fail": fp, "judge_fail_human_pass": fn},
            "mismatches": mismatches[:50], "calibrated_at": datetime.now(timezone.utc).isoformat()}


def load_calibration(path: str | Path | None) -> dict[str, Any]:
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {d["grader"]: d for d in (data if isinstance(data, list) else data.get("graders", [data]))}


def untrusted_graders(calibration: dict[str, Any], min_kappa: float | None) -> dict[str, str]:
    if min_kappa is None:
        return {}
    return {g: f"kappa {d['kappa']:.2f} < {min_kappa}" for g, d in calibration.items() if d.get("kappa") is not None and d["kappa"] < min_kappa}
