"""Trajectory graders for tool-calling agents."""
from __future__ import annotations

from typing import Any

from ..models import Case, Sample, Score, ToolCall
from .base import Grader, dig, register
from .deterministic import _amount, _norm


def args_match(actual: dict[str, Any], expected: dict[str, Any], numeric_tol: float = 0.0) -> bool:
    """Expected args are a subset of actual args (case/whitespace-insensitive strings, tolerant numbers)."""
    for k, v in (expected or {}).items():
        if k not in actual:
            return False
        a = actual[k]
        if isinstance(v, dict) and isinstance(a, dict):
            if not args_match(a, v, numeric_tol):
                return False
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            av = _amount(a)
            if av is None or abs(av - v) > numeric_tol * max(1.0, abs(v)):
                return False
        elif isinstance(v, list):
            if sorted(map(_norm, v)) != sorted(map(_norm, a if isinstance(a, list) else [a])):
                return False
        elif _norm(a) != _norm(v):
            return False
    return True


def _calls(sample: Sample, include_handoffs: bool = False) -> list[ToolCall]:
    kinds = {"tool", "handoff"} if include_handoffs else {"tool"}
    return [t for t in sample.trajectory if t.kind in kinds]


@register("tool_called")
class ToolCalled(Grader):
    """tool: name; args: subset (optional); min / max times."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        name = self.p.get("tool") or dig(case.reference, "tool")
        calls = [c for c in _calls(sample) if c.name == name and args_match(c.args, self.p.get("args", {}), self.p.get("numeric_tolerance", 0))]
        n = len(calls)
        ok = int(self.p.get("min", 1)) <= n <= int(self.p.get("max", 10**6))
        return self.score(sample, 1.0 if ok else 0.0, details={"tool": name, "times": n})


@register("tool_not_called")
class ToolNotCalled(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        names = self.p.get("tools") or [self.p.get("tool")]
        hit = [c.name for c in _calls(sample) if c.name in names]
        return self.score(sample, 0.0 if hit else 1.0, details={"forbidden_called": hit})


@register("tool_args_match")
class ToolArgsMatch(Grader):
    """Each expected call {name, args} in case.reference.tool_calls must be matched by some actual call (subset args)."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        expected = self.p.get("calls") or dig(case.reference, self.p.get("reference_key", "tool_calls")) or []
        if not expected:
            return self.skip(sample, "no expected tool calls")
        actual = _calls(sample)
        used: set[int] = set()
        matched, missing = 0, []
        for e in expected:
            idx = next((i for i, c in enumerate(actual) if i not in used and c.name == e["name"]
                        and args_match(c.args, e.get("args", {}), self.p.get("numeric_tolerance", 0))), None)
            if idx is None:
                missing.append(e)
            else:
                used.add(idx)
                matched += 1
        return self.score(sample, matched / len(expected), details={"missing": missing, "actual": [c.name for c in actual]})


def _is_subsequence(seq: list[str], sub: list[str]) -> bool:
    it = iter(seq)
    return all(any(x == y for y in it) for x in sub)


@register("tool_sequence")
class ToolSequence(Grader):
    """mode: exact | subsequence (default) | unordered. Sequence from `sequence` or case.reference.tool_sequence."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        exp = self.p.get("sequence") or dig(case.reference, self.p.get("reference_key", "tool_sequence"))
        if not exp:
            return self.skip(sample, "no expected sequence")
        got = [c.name for c in _calls(sample, include_handoffs=self.p.get("include_handoffs", False))]
        mode = self.p.get("mode", "subsequence")
        ok = (got == exp) if mode == "exact" else (sorted(set(exp)) == sorted(set(got) & set(exp))) if mode == "unordered" \
            else _is_subsequence(got, exp)
        return self.score(sample, 1.0 if ok else 0.0, details={"expected": exp, "got": got, "mode": mode})


@register("final_state")
class FinalState(Grader):
    """Compare the agent's final environment state (e.g. mock claims DB) with case.reference.final_state (subset)."""

    async def grade(self, sample: Sample, case: Case) -> Score:
        exp = self.p.get("expected") or dig(case.reference, self.p.get("reference_key", "final_state"))
        if exp is None:
            return self.skip(sample, "no expected final state")
        ok = args_match(sample.final_state or {}, exp, self.p.get("numeric_tolerance", 0.0))
        return self.score(sample, 1.0 if ok else 0.0, details={"expected": exp, "got": sample.final_state})


@register("step_budget")
class StepBudget(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        max_steps = int(self.p.get("max_steps", 10))
        max_calls = self.p.get("max_tool_calls")
        n_calls = len(_calls(sample))
        dup = len(_calls(sample)) - len({(c.name, str(sorted(c.args.items()))) for c in _calls(sample)})
        ok = sample.steps <= max_steps and (max_calls is None or n_calls <= int(max_calls)) and dup <= int(self.p.get("max_duplicate_calls", 1))
        return self.score(sample, 1.0 if ok else 0.0, details={"steps": sample.steps, "tool_calls": n_calls, "duplicate_calls": dup})


@register("handoff_to")
class HandoffTo(Grader):
    async def grade(self, sample: Sample, case: Case) -> Score:
        agent = self.p.get("agent") or dig(case.reference, "handoff_to")
        if agent is None:
            return self.skip(sample, "no expected handoff")
        hands = [c for c in sample.trajectory if c.kind == "handoff"]
        hit = any(agent in (c.name or "") or c.args.get("to") == agent for c in hands)
        if agent in ("none", "", False):
            hit = not hands
        return self.score(sample, 1.0 if hit else 0.0, details={"handoffs": [c.args.get("to") or c.name for c in hands]})
