"""Release gates: a small, safe expression language evaluated per target.

    mean(faithfulness) >= 0.90 and ci_low(faithfulness) >= 0.85
    delta(task_success) > -0.03 or not significant(task_success)
    critical_pass()
    p95(latency_ms) <= 3000 and mean(cost_usd) <= 0.02
    pass_hat_k(task_success) >= 0.8
    mean(field_accuracy, tag="lang:hi") >= 0.85

Functions (grader names or ops metrics as bare words):
  mean ci_low ci_high pass_rate pass_at_k pass_hat_k   - grader aggregates (mean also accepts latency_ms / ttft_ms / cost_usd / tokens / steps)
  p50 p95 p99 total                                     - ops metrics
  delta delta_ci_low delta_ci_high significant          - paired comparison with the baseline run for the same target
  critical_pass()  error_rate()

A gate is **invalid** (and blocks, when severity is block) instead of passing or failing when it cannot be decided:
no baseline for delta()/significant() (unless allow_missing_baseline), fewer than min_n cases behind a metric or
slice, no scores for a grader, or a judge that failed calibration.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .stats import metric_series, paired_delta, percentile

GRADER_FUNCS = {"mean", "ci_low", "ci_high", "pass_rate", "pass_at_k", "pass_hat_k"}
OPS_FUNCS = {"p50", "p95", "p99", "total"}
DELTA_FUNCS = {"delta", "delta_ci_low", "delta_ci_high", "significant"}
NULLARY = {"critical_pass", "error_rate"}
OPS_METRICS = {"latency_ms", "ttft_ms", "cost_usd", "tokens", "steps"}
ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare, ast.Gt, ast.GtE, ast.Lt,
                 ast.LtE, ast.Eq, ast.NotEq, ast.Call, ast.Name, ast.Load, ast.Constant, ast.keyword, ast.BinOp, ast.Add, ast.Sub,
                 ast.Mult, ast.Div)


class GateError(ValueError):
    pass


def _parse(rule: str) -> ast.Expression:
    try:
        tree = ast.parse(rule, mode="eval")
    except SyntaxError as e:
        raise GateError(f"syntax error: {e.msg}") from e
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise GateError(f"'{type(node).__name__}' is not allowed in gate rules")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise GateError("only plain function calls are allowed")
    return tree


def referenced(rule: str) -> tuple[set[str], set[str]]:
    """(functions, metric/grader names) used by a rule."""
    tree = _parse(rule)
    funcs, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            funcs.add(node.func.id)
            for a in node.args:
                if isinstance(a, ast.Name):
                    names.add(a.id)
    return funcs, names


def validate_rule(rule: str, grader_names: set[str]) -> list[str]:
    try:
        funcs, names = referenced(rule)
    except GateError as e:
        return [str(e)]
    problems = []
    for f in funcs - GRADER_FUNCS - OPS_FUNCS - DELTA_FUNCS - NULLARY:
        problems.append(f"unknown function '{f}'")
    for n in names - grader_names - OPS_METRICS:
        problems.append(f"unknown grader or metric '{n}'")
    return problems


@dataclass
class GateResult:
    gate: str
    target: str
    rule: str
    severity: str
    passed: bool
    status: str = "pass"                  # pass | fail | invalid
    values: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class _Evaluator:
    def __init__(self, summary: dict, samples: list, baseline: dict | None, seed: int, untrusted: dict[str, str],
                 min_n: int = 1, allow_missing_baseline: bool = False):
        self.s, self.samples, self.base, self.seed, self.untrusted = summary, samples, baseline, seed, untrusted
        self.min_n, self.allow_missing_baseline = min_n, allow_missing_baseline
        self.values: dict[str, Any] = {}
        self.invalid: list[str] = []

    def _g(self, name: str) -> dict:
        g = self.s["graders"].get(name)
        if g is None:
            raise GateError(f"no scores for '{name}' on target '{self.s['target']}'")
        if name in self.untrusted:
            self.invalid.append(f"judge for '{name}' failed calibration ({self.untrusted[name]})")
        if (g.get("n") or 0) < self.min_n:
            raise GateError(f"only {g.get('n') or 0} case(s) scored for '{name}' (min_n {self.min_n})")
        return g

    def call(self, fn: str, args: list[Any], kw: dict[str, Any]) -> Any:
        key = f"{fn}({', '.join(map(str, args))}{', ' + ', '.join(f'{k}={v!r}' for k, v in kw.items()) if kw else ''})"
        if fn in NULLARY:
            if fn == "critical_pass":
                v = not self.s["critical"]["failed"]
            else:
                ops = self.s["ops"]
                v = ops["errors"] / ops["samples"] if ops["samples"] else 0.0
        elif fn in OPS_FUNCS or (fn == "mean" and args and args[0] in OPS_METRICS and args[0] not in self.s["graders"]):
            series = metric_series(self.s, self.samples, args[0])
            if fn == "total":
                v = sum(series)
            elif fn == "mean":
                v = sum(series) / len(series) if series else None
            else:
                v = percentile(series, {"p50": .5, "p95": .95, "p99": .99}[fn])
        elif fn in GRADER_FUNCS:
            g = self._g(args[0])
            if kw.get("tag"):
                v = self.s["slices"].get(kw["tag"], {}).get(args[0]) if fn == "mean" else None
                if v is None:
                    raise GateError(f"no '{args[0]}' scores for tag '{kw['tag']}'")
                n = self.s.get("slice_n", {}).get(kw["tag"], {}).get(args[0], 0)
                if n < self.min_n:
                    raise GateError(f"slice '{kw['tag']}' has only {n} case(s) for '{args[0]}' (min_n {self.min_n})")
            else:
                v = g[fn]
        elif fn in DELTA_FUNCS:
            g = self._g(args[0])
            bg = (self.base or {}).get("graders", {}).get(args[0])
            if not bg:
                if not self.allow_missing_baseline:
                    why = "no baseline run" if not self.base else f"baseline has no '{args[0]}' scores"
                    raise GateError(f"{why}: {fn}() cannot be decided (set allow_missing_baseline: true on the gate "
                                    "to let it pass until a baseline exists)")
                self.values[key] = "no baseline"
                return {"delta": 0.0, "delta_ci_low": 0.0, "delta_ci_high": 0.0, "significant": False}[fn]
            alpha = float(kw.get("alpha", 0.05))
            d = paired_delta(g["per_case"], bg["per_case"], self.seed, g.get("per_case_pass"), bg.get("per_case_pass"), alpha=alpha)
            if d["n"] < self.min_n:
                raise GateError(f"only {d['n']} case(s) shared with the baseline for '{args[0]}' (min_n {self.min_n})")
            v = {"delta": d["delta"], "delta_ci_low": d["ci_low"], "delta_ci_high": d["ci_high"], "significant": d["significant"]}[fn]
        else:
            raise GateError(f"unknown function '{fn}'")
        self.values[key] = v
        if v is None:
            raise GateError(f"{key} has no value")
        return v

    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.eval(node.body)
        if isinstance(node, ast.BoolOp):
            vals = [self.eval(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.UnaryOp):
            v = self.eval(node.operand)
            return (not v) if isinstance(node.op, ast.Not) else -v
        if isinstance(node, ast.BinOp):
            a, b = self.eval(node.left), self.eval(node.right)
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Div: a / b if b else float("inf")}[type(node.op)]
        if isinstance(node, ast.Compare):
            left = self.eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = self.eval(comp)
                ok = {ast.Gt: left > right, ast.GtE: left >= right, ast.Lt: left < right, ast.LtE: left <= right,
                      ast.Eq: left == right, ast.NotEq: left != right}[type(op)]
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args = [a.id if isinstance(a, ast.Name) else self.eval(a) for a in node.args]
            kw = {k.arg: self.eval(k.value) for k in node.keywords}
            return self.call(node.func.id, args, kw)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            raise GateError(f"bare name '{node.id}' outside a function call")
        raise GateError(f"unsupported expression {type(node).__name__}")


def evaluate_gates(gates: list, summaries: dict[str, dict], samples: list, baselines: dict[str, dict] | None = None,
                   seed: int = 7, untrusted: dict[str, str] | None = None, min_n: int = 1) -> list[GateResult]:
    out: list[GateResult] = []
    for g in gates:
        for tname, summary in summaries.items():
            if g.targets and tname not in g.targets:
                continue
            ev = _Evaluator(summary, samples, (baselines or {}).get(tname), seed, untrusted or {},
                            min_n=g.min_n if getattr(g, "min_n", None) is not None else min_n,
                            allow_missing_baseline=getattr(g, "allow_missing_baseline", False))
            try:
                ok = bool(ev.eval(_parse(g.rule)))
                if ev.invalid:
                    out.append(GateResult(g.name, tname, g.rule, g.severity, False, "invalid", ev.values, "; ".join(ev.invalid)))
                else:
                    out.append(GateResult(g.name, tname, g.rule, g.severity, ok, "pass" if ok else "fail", ev.values))
            except GateError as e:
                out.append(GateResult(g.name, tname, g.rule, g.severity, False, "invalid", ev.values, str(e)))
    return out


def blocking_failures(results: list[GateResult]) -> list[GateResult]:
    return [r for r in results if r.severity == "block" and not r.passed]
