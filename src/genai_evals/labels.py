"""Human labelling: a reviewer sheet sampled from a run, and a labelling guide built from a grader's rubric.

OpenAI's evaluation best practices recommend several review rounds, "show rather than tell" examples at each score
level, pass/fail thresholds alongside numeric scores, and consensus across reviewers. This module produces the two
artefacts reviewers work from; `calibration` combines their labels and measures agreement.

The sheet is a CSV with one row per (item, reviewer): case_id, target, repeat, tags, input, output, reviewer, round,
label, adjudicated, notes. Inputs and outputs are copied from the result, which is already tokenised when the run used
tokenisation, so the sheet carries no more personal data than the result does.
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

SHEET_COLUMNS = ["case_id", "target", "repeat", "tags", "input", "output", "judge_passed", "reviewer", "round", "label",
                 "adjudicated", "notes", "sample_design", "stratum", "weight"]


def sample_items(result: dict[str, Any], grader: str, n: int = 50, seed: int = 7, stratify: bool = True,
                 include_judge: bool = False, design: str | None = None) -> list[dict[str, Any]]:
    """Pick units to label. **The unit is (case, target)**: one human label per case and target. When the run had
    repeats, one repeat is chosen at random for each unit, recorded in the `repeat` column, and calibration compares
    the label with the judge's verdict on that repeat only, so repeats never count as extra human comparisons.

    design="stratified" (calibration, the default): units are stratified by tag and by the judge's verdict on the
    chosen repeat, drawn round-robin so rare slices and both outcomes are represented. Not representative of
    production.
    design="random" (validation): a simple random sample of units.

    Each row records its design, stratum and inverse-inclusion weight (units in the stratum / units drawn from it),
    so calibration can weight agreement back to the population and resample within strata for its intervals."""
    design = design or ("stratified" if stratify else "random")
    stratify = design == "stratified"
    cases = {c["id"]: c for c in result.get("cases", [])}
    samples = {(s["case_id"], s["target"], s.get("repeat", 0)): s for s in result.get("samples", [])}
    scored = [s for s in result["scores"] if s["grader"] == grader and not s.get("skipped") and s.get("passed") is not None]
    if not scored:
        raise ValueError(f"no scored items for grader '{grader}' in this result")
    rng = random.Random(seed)
    units: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in scored:
        units[(s["case_id"], s["target"])].append(s)
    chosen = [rng.choice(sorted(v, key=lambda x: x.get("repeat", 0))) for _, v in sorted(units.items())]

    def stratum(s):
        tags = cases.get(s["case_id"], {}).get("tags") or ["(untagged)"]
        return (sorted(tags)[0], bool(s["passed"])) if stratify else ("all", None)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in chosen:
        groups[stratum(s)].append(s)
    sizes = {k: len(v) for k, v in groups.items()}
    for g in groups.values():
        rng.shuffle(g)
    picked: list[dict] = []
    keys = sorted(groups, key=str)
    while len(picked) < min(n, len(chosen)):          # round-robin across strata
        for k in keys:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop())
    drawn: dict[tuple, int] = defaultdict(int)
    for s in picked:
        drawn[stratum(s)] += 1
    out = []
    for s in picked:
        k = stratum(s)
        smp = samples.get((s["case_id"], s["target"], s.get("repeat", 0)), {})
        case = cases.get(s["case_id"], {})
        out.append({"case_id": s["case_id"], "target": s["target"], "repeat": s.get("repeat", 0),
                    "tags": ";".join(case.get("tags") or []), "input": smp.get("input") or case.get("input") or "",
                    "output": smp.get("output") or "", "judge_passed": s["passed"] if include_judge else "",
                    "sample_design": design, "stratum": "|".join(map(str, k)) if stratify else "all",
                    "weight": round(sizes[k] / drawn[k], 6)})
    return out


def write_sheet(items: list[dict[str, Any]], path: str | Path, reviewers: list[str] | None = None,
                round_: int = 1) -> Path:
    """One row per item and reviewer. Leave judge verdicts out of what reviewers see (the default) so labels stay
    independent of the judge."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for it in items:
        for r in (reviewers or [""]):
            rows.append(it | {"reviewer": r, "round": round_, "label": "", "adjudicated": "", "notes": ""})
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def write_consensus(labels: dict[tuple[str, str], bool], path: str | Path) -> Path:
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "target", "label"])
        for (cid, tgt), lab in sorted(labels.items()):
            w.writerow([cid, tgt, "pass" if lab else "fail"])
    return path


def labelling_guide(suite, grader_name: str, examples: list[dict[str, Any]] | None = None) -> str:
    """A Markdown guide for reviewers of one grader: what to judge, the pass threshold, and an example at each level
    (from the grader's `anchors`, or placeholders to fill in). Show, don't tell: reviewers agree more when each level
    has a worked example."""
    spec = next((g for g in suite.graders if g.name == grader_name), None)
    if spec is None:
        raise ValueError(f"grader '{grader_name}' is not in suite '{suite.name}'")
    p = spec.params()
    scale = p.get("scale", "pass_fail" if spec.type in ("reference_match", "policy", "correct_refusal") else "1-5")
    criteria = p.get("rubric") or p.get("criteria") or p.get("policy") or "(describe what a good output does)"
    lines = [f"# Labelling guide: {grader_name}", "",
             f"Suite: `{suite.name}` · grader type: `{spec.type}` · scale: `{scale}`", "",
             "## What you are judging", "", str(criteria).strip(), "",
             "## How to label", "",
             "- Label each row `pass` or `fail` in the `label` column. Use `unsure` if you cannot decide; it is ignored.",
             "- Judge the output against the criteria above, not against how you would have written it.",
             "- Length is not quality: a short, complete answer passes; padding does not earn credit.",
             "- Write a short reason in `notes` for every `fail` and every `unsure`.",
             "- Do not discuss items with other reviewers until everyone has finished the round.", ""]
    if scale == "1-5":
        thr = spec.threshold if spec.threshold is not None else 0.75
        lvl = 1 + 4 * thr
        lines += [f"A score of **{lvl:g} or more out of 5** counts as a pass (the grader's threshold is {thr:g} on a 0–1 "
                  "scale). If you score 1–5 in `notes`, also put pass/fail in `label`.", ""]
    lines += ["## Examples at each level", ""]
    anchors = p.get("anchors") or {}
    levels = list(anchors) if anchors else (["5", "4", "3", "2", "1"] if scale == "1-5" else ["pass", "fail"])
    for lv in levels:
        lines += [f"### {lv}", "", str(anchors.get(lv, "_Describe what an output at this level looks like._")), "",
                  "Example input: _…_", "", "Example output: _…_", "", "Why: _…_", ""]
    if examples:
        lines += ["## Worked examples from this suite", ""]
        for ex in examples[:6]:
            lines += [f"**{ex['case_id']}** ({ex.get('tags') or 'untagged'})", "", f"> {ex['input'][:400]}", "",
                      f"Output: {ex['output'][:600]}", "", "Label: _…_ · Why: _…_", ""]
    lines += ["## Rounds and disputes", "",
              "1. Round 1: every item is labelled by at least two reviewers, independently.",
              "2. `genai-evals labels agreement` reports agreement and lists disputed items.",
              "3. Round 2: discuss the disputed items, update this guide where it was unclear, and re-label "
              "(set `round` to 2).",
              "4. Items still disputed are settled by an adjudicator (a row with `adjudicated` = true).",
              "5. Calibrate the judge only on the consensus labels: `genai-evals calibrate … --labels reviews.csv`.", ""]
    return "\n".join(lines)
