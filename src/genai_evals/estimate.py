"""What a run will cost before it starts (`genai-evals run --dry-run`, `--budget-usd`).

Counts the model calls a run makes (targets that call a model through a provider profile, judges, the simulated user)
and prices them from the provider profiles' `pricing`. Token counts are estimates from text length (about 4
characters a token) and the prompt templates, and the call counts are an upper bound: conversations are counted at
their turn limit, and graders that would skip a sample (no context, no reference) are counted anyway. Systems you
call over HTTP or Python, and managed agents, are counted as requests but not priced: their cost is the customer's
own deployment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JUDGE_TYPES = {"rubric", "checklist", "reference_match", "policy", "faithfulness", "answer_relevance",
               "context_precision", "context_recall", "pairwise", "trajectory_rubric"}
JUDGE_PROMPT_TOKENS = 700        # instructions, rubric and output schema
JUDGE_OUTPUT_TOKENS = 200        # short rationale + verdict (judges are told to keep rationales short)
TARGET_OUTPUT_TOKENS = 400
CHUNK_TOKENS = 200
TOOL_LOOP_CALLS_PER_TURN = 3     # a model call, a tool call or two, the answer


def _tok(text: str) -> int:
    return max(1, len(text or "") // 4)


@dataclass
class Line:
    role: str                    # target | judge | simulator | system
    name: str                    # target or grader name
    profile: str | None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)


def _turns(case: Any) -> int:
    if case.user_rules or case.goal:
        return int(case.max_turns or 6)
    if case.turns:
        return int(case.max_turns or len(case.turns))
    return 1


def _price(providers: Any, profile: str | None, inp: int, out: int) -> float | None:
    if not profile or profile not in providers.profiles:
        return None
    prof = providers.get(profile)
    if prof.provider == "mock":
        return 0.0                                  # the offline stand-ins cost nothing
    p = prof.pricing
    if not p or (not p.input_per_mtok and not p.output_per_mtok):
        return None                                 # unset (or left at 0): don't pretend it's free
    return (inp * p.input_per_mtok + out * p.output_per_mtok) / 1e6


def estimate(suite: Any, providers: Any, cases: list[Any]) -> dict[str, Any]:
    repeats = max(1, suite.repeats)
    lines: list[Line] = []
    out_len: dict[str, int] = {}
    for t in suite.targets:
        model_backed = t.kind in ("model", "prompt", "rag") or (t.kind == "agent" and t.framework == "tool_loop")
        ln = Line("target" if model_backed else "system", t.name, t.profile if model_backed else None)
        sys_tok = _tok(t.system or "") if t.system else 300
        for c in cases:
            turns = _turns(c)
            per_turn = TOOL_LOOP_CALLS_PER_TURN if t.kind == "agent" and t.framework == "tool_loop" else 1
            n = turns * per_turn * repeats
            ctx = (t.options.get("top_k", 4) * CHUNK_TOKENS) if t.kind == "rag" else 0
            ln.calls += n
            # each call in a conversation re-sends the conversation so far: grow linearly with the turn
            hist = sum(range(turns)) * (_tok(c.input_text()) + TARGET_OUTPUT_TOKENS)
            ln.input_tokens += repeats * (turns * per_turn * (sys_tok + _tok(c.input_text()) + ctx) + per_turn * hist)
            ln.output_tokens += n * TARGET_OUTPUT_TOKENS
        if model_backed:
            ln.cost_usd = _price(providers, t.profile, ln.input_tokens, ln.output_tokens)
            if ln.cost_usd is None:
                ln.notes.append(f"no pricing (or 0) on profile '{t.profile}'")
        else:
            ln.input_tokens = ln.output_tokens = 0
            ln.notes.append("your system's own model cost is not included")
        lines.append(ln)
        out_len[t.name] = TARGET_OUTPUT_TOKENS
    n_targets = max(1, len(suite.targets))
    for g in suite.graders:
        if g.type not in JUDGE_TYPES:
            continue
        judges = list(g.judges or suite.judges or [])
        params = g.params() if hasattr(g, "params") else {}
        applicable = [c for c in cases if not g.tags or set(g.tags) & set(c.tags or [])]
        for j in judges:
            ln = Line("judge", g.name, j)
            for c in applicable:
                per = 1
                if g.type == "checklist":
                    per = max(1, len(params.get("criteria") or []))
                if g.type == "pairwise":
                    per = 2                                 # both orders, to control position bias
                if params.get("per_turn"):
                    per *= _turns(c)
                samples = repeats * (1 if g.type == "pairwise" else n_targets)
                ctx = 4 * CHUNK_TOKENS if g.type in ("faithfulness", "context_precision", "context_recall") else 0
                ln.calls += per * samples
                ln.input_tokens += per * samples * (JUDGE_PROMPT_TOKENS + _tok(c.input_text()) + TARGET_OUTPUT_TOKENS + ctx)
                ln.output_tokens += per * samples * JUDGE_OUTPUT_TOKENS
            ln.cost_usd = _price(providers, j, ln.input_tokens, ln.output_tokens)
            if ln.cost_usd is None:
                ln.notes.append(f"no pricing (or 0) on profile '{j}'")
            lines.append(ln)
    if suite.simulator and suite.simulator != "rules":
        ln = Line("simulator", "simulated user", suite.simulator)
        for c in cases:
            if c.goal and not c.turns:
                n = _turns(c) * repeats * n_targets
                ln.calls += n
                ln.input_tokens += n * (400 + _turns(c) * 150)
                ln.output_tokens += n * 80
        if ln.calls:
            ln.cost_usd = _price(providers, suite.simulator, ln.input_tokens, ln.output_tokens)
            lines.append(ln)
    priced = [ln.cost_usd for ln in lines if ln.cost_usd is not None]
    unpriced = [ln for ln in lines if ln.cost_usd is None and ln.role != "system" and ln.calls]
    model_calls = sum(ln.calls for ln in lines if ln.role != "system")
    return {
        "cases": len(cases), "repeats": repeats, "targets": n_targets,
        "lines": [ln.__dict__ for ln in lines],
        "model_calls": model_calls,
        "system_requests": sum(ln.calls for ln in lines if ln.role == "system"),
        "estimated_cost_usd": round(sum(priced), 4) if priced else (0.0 if not unpriced and not model_calls else None),
        "complete": not unpriced,
        "unpriced_profiles": sorted({ln.profile for ln in unpriced if ln.profile}),
    }


def render(est: dict[str, Any]) -> str:
    rows = [f"Plan: {est['cases']} case(s) x {est['repeats']} repeat(s) x {est['targets']} target(s)"]
    for ln in est["lines"]:
        if not ln["calls"]:
            continue
        cost = "—" if ln["role"] == "system" else ("unknown" if ln["cost_usd"] is None else f"${ln['cost_usd']:.2f}")
        tok = "" if ln["role"] == "system" else f", ~{ln['input_tokens'] + ln['output_tokens']:,} tokens"
        who = f"{ln['role']} {ln['name']}" + (f" ({ln['profile']})" if ln["profile"] else "")
        rows.append(f"  {who}: {ln['calls']:,} call(s){tok}, {cost}" + (f"  [{'; '.join(ln['notes'])}]" if ln["notes"] else ""))
    total = est["estimated_cost_usd"]
    rows.append(f"Estimated model cost: {'unknown' if total is None else f'${total:.2f}'}"
                + ("" if est["complete"] else f" (plus unpriced calls on {', '.join(est['unpriced_profiles'])}: add "
                                              "`pricing:` to those profiles)")
                + ". An upper bound: conversations at their turn limit, every grader on every sample.")
    return "\n".join(rows)
