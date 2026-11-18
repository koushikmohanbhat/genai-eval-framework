"""Is a result about the system, or about how the rubric was worded?

Re-grade the same samples with reworded versions of a judge's rubric (or policy, or checklist) and compare: how
often the verdict flips, the agreement (Cohen's kappa) with the original verdicts, the change in the mean, and
whether any gate outcome changes. If rewording the rubric changes the conclusion, the eval measured the grader.
Following Anthropic's "Building strong safety evaluations".
"""
from __future__ import annotations

import copy
from typing import Any

#: the field a one-line --variant replaces, by grader type
TEXT_FIELD = {"rubric": "rubric", "reference_match": "rubric", "trajectory_rubric": "rubric", "policy": "policy"}


def _verdicts(result: dict[str, Any], grader: str) -> dict[tuple[str, str, int], bool]:
    return {(s["case_id"], s["target"], int(s.get("repeat") or 0)): bool(s["passed"]) for s in result["scores"]
            if s["grader"] == grader and not s.get("skipped") and s.get("passed") is not None}


def _kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    from .calibration import cohen_kappa
    return cohen_kappa(pairs) if len(pairs) >= 2 else None


def _gates(result: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(g["gate"], g["target"]): g.get("status") or ("pass" if g["passed"] else "fail") for g in result.get("gates", [])}


def variant_overrides(spec_type: str, variants: list[Any]) -> list[dict[str, Any]]:
    """Strings become {name, <text field>: text}; dicts are used as given (any grader parameters)."""
    field = TEXT_FIELD.get(spec_type)
    out = []
    for i, v in enumerate(variants):
        if isinstance(v, str):
            if not field:
                raise ValueError(f"a one-line variant needs a rubric or policy grader (this one is '{spec_type}'): "
                                 "give variants as parameter dicts instead")
            out.append({"name": f"variant-{i + 1}", field: v})
        else:
            d = dict(v)
            d.setdefault("name", f"variant-{i + 1}")
            out.append(d)
    return out


def sensitivity(suite: Any, providers: Any, result: dict[str, Any], grader: str, variants: list[Any],
                baseline: dict[str, Any] | None = None, attempts: int = 2, backoff_s: float = 2.0,
                max_flip_rate: float = 0.1) -> dict[str, Any]:
    from .runner import Runner, SuiteError
    spec = next((g for g in suite.graders if g.name == grader), None)
    if spec is None:
        raise SuiteError(f"grader '{grader}' is not in the suite")
    original = _verdicts(result, grader)
    orig_mean = _mean(result, grader)
    orig_gates = _gates(result)
    skipped_gates: list[str] = []
    if baseline is None:
        # without the baseline, delta() gates can't be decided on the re-graded result: leave them out of the comparison
        from .gates import DELTA_FUNCS
        delta_gates = {g.name for g in suite.gates if any(f"{fn}(" in g.rule for fn in DELTA_FUNCS)}
        skipped_gates = sorted(delta_gates & {g for g, _ in orig_gates})
        orig_gates = {k: v for k, v in orig_gates.items() if k[0] not in delta_gates}
    reports = []
    for ov in variant_overrides(spec.type, variants):
        name = ov.pop("name")
        s2 = copy.deepcopy(suite)
        g2 = next(g for g in s2.graders if g.name == grader)
        for k, v in ov.items():
            setattr(g2, k, v)
        new = Runner(s2, providers, use_cache=False).regrade(result, graders=[grader], only="all", attempts=attempts,
                                                             backoff_s=backoff_s, force=True, baseline=baseline)
        nv = _verdicts(new, grader)
        both = sorted(set(original) & set(nv))
        pairs = [(original[k], nv[k]) for k in both]
        flips = [k for k in both if original[k] != nv[k]]
        gates_new = _gates(new)
        changed = sorted(f"{g} @ {t}: {orig_gates[(g, t)]} -> {gates_new.get((g, t))}"
                         for (g, t) in orig_gates if gates_new.get((g, t)) != orig_gates[(g, t)])
        flip_rate = len(flips) / len(both) if both else None
        reports.append({"variant": name, "overrides": ov, "units": len(both), "flips": len(flips),
                        "flip_rate": flip_rate, "pass_to_fail": sum(1 for k in flips if original[k]),
                        "fail_to_pass": sum(1 for k in flips if not original[k]), "kappa": _kappa(pairs),
                        "mean_original": orig_mean, "mean_variant": _mean(new, grader), "gates_changed": changed,
                        "flipped_units": [f"{c}@{t}#{r}" for c, t, r in flips[:20]],
                        "robust": not changed and (flip_rate is not None and flip_rate <= max_flip_rate)})
    return {"grader": grader, "variants": reports, "max_flip_rate": max_flip_rate,
            "gates_not_compared": skipped_gates,
            "robust": all(r["robust"] for r in reports) if reports else None}


def _mean(result: dict[str, Any], grader: str) -> float | None:
    vals = [d["graders"][grader]["mean"] for d in (result.get("summary") or {}).values()
            if grader in d.get("graders", {}) and d["graders"][grader].get("mean") is not None]
    return sum(vals) / len(vals) if vals else None
