"""Standard case tags for coverage, following the edge cases in OpenAI's evaluation best practices.

Tag cases with these (alongside your own domain tags) so a suite can be checked for what it does not yet test, and so
results can be read per slice (`summary.slices` breaks every grader down by tag). `edge` and `adversarial` also put a
case in the **stress** split, reported and gated apart from the **representative** split, so stress cases never
distort the production estimate. `genai-evals doctor` and
`genai-evals cases coverage` report coverage against the tags that apply to the pack's workflow.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

# tag -> (group, description, workflows it applies to)
ALL = ("rag", "agent", "extraction", "single_turn")
TAGS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # how hard / how hostile the case is
    "typical": ("difficulty", "an ordinary request, as most production traffic looks", ALL),
    "edge": ("difficulty", "an unusual but legitimate request near a boundary of the policy or the data", ALL),
    "adversarial": ("difficulty", "a request trying to make the system misbehave", ALL),
    # input variability
    "multilingual": ("input", "non-English or mixed-language input (for India: Hindi, Hinglish, regional languages)", ALL),
    "structured_input": ("input", "input in a structured format: JSON, XML, Markdown, CSV, a table", ALL),
    "image_input": ("input", "input that includes an image or a scanned document", ("rag", "agent", "extraction")),
    "noisy_input": ("input", "OCR errors, bad scans, typos and missing fields in the source", ("extraction",)),
    # contextual complexity
    "multi_intent": ("context", "two or more requests in one message", ("rag", "agent", "single_turn")),
    "typos": ("context", "misspellings and informal shorthand", ("rag", "agent", "single_turn")),
    "very_short": ("context", "a very short or underspecified request", ("rag", "agent", "single_turn")),
    "long_conversation": ("context", "many turns, where earlier context matters", ("agent",)),
    "ambiguous_tool_output": ("context", "a tool returns something ambiguous, empty or an error", ("agent",)),
    "bad_tool_args": ("context", "the user gives wrong, missing or several conflicting values for a tool argument", ("agent",)),
    "circular_handoff": ("context", "a request that could bounce between agents (multi-agent systems)", ("agent",)),
    "no_answer": ("context", "the answer is not in the knowledge base", ("rag",)),
    # personalisation and safety
    "jailbreak": ("safety", "an attempt to get around the instructions", ALL),
    "system_conflict": ("safety", "the user asks for something the system prompt forbids; the system prompt must win", ALL),
    "format_request": ("safety", "the user asks for a specific output format or style", ("rag", "agent", "single_turn")),
}

GROUPS = ("difficulty", "input", "context", "safety")
WORKFLOW_ALIASES = {"tool-agent": "agent", "agent": "agent", "rag": "rag", "rag-knowledge-assistant": "rag",
                    "extraction": "extraction", "structured-extraction": "extraction", "single_turn": "single_turn"}


def workflow_of(suite: Any) -> str:
    """The suite's workflow, from its pack (`pack: tool-agent@0.1.0`) or an explicit `workflow:` field."""
    wf = getattr(suite, "workflow", None) or (getattr(suite, "model_extra", None) or {}).get("workflow")
    if not wf and getattr(suite, "pack", None):
        wf = str(suite.pack).split("@")[0]
    return WORKFLOW_ALIASES.get(str(wf or ""), "single_turn")


def applicable(workflow: str | None) -> list[str]:
    wf = WORKFLOW_ALIASES.get(str(workflow or ""), "single_turn")
    return [t for t, (_g, _d, wfs) in TAGS.items() if wf in wfs]


def coverage(cases: list[Any], workflow: str | None = None, min_per_tag: int = 1, min_representative: int = 30,
             stress_tags: list[str] | tuple[str, ...] = ("edge", "adversarial")) -> dict[str, Any]:
    """Two sets, checked separately:

    - the **representative** set (cases without a stress tag, or `split: representative`): production-like traffic,
      ideally sampled from production and weighted by prevalence. Its score estimates production performance, so it
      needs enough cases (`min_representative`, default 30) and no edge cases mixed in;
    - the **stress** suite (`edge` / `adversarial`, or `split: stress`): written to find failures. Coverage tags say
      which kinds of stress case are missing.

    Cases may be Case objects or dicts with `tags` (and optionally `split`)."""
    def tags_of(c):
        return list(getattr(c, "tags", None) or (c.get("tags") if isinstance(c, dict) else None) or [])

    def split_of(c):
        sp = getattr(c, "split", None) if not isinstance(c, dict) else c.get("split")
        if sp in ("representative", "stress"):
            return sp
        return "stress" if set(tags_of(c)) & set(stress_tags) else "representative"
    counts = Counter(t for c in cases for t in set(tags_of(c)))
    app = applicable(workflow)
    present = {t: counts[t] for t in app if counts[t] >= min_per_tag}
    missing = [t for t in app if counts[t] < min_per_tag]
    n = len(cases)
    rep = sum(1 for c in cases if split_of(c) == "representative")
    stress = n - rep
    untagged = sum(1 for c in cases if not {"typical", "edge", "adversarial"} & set(tags_of(c))
                   and getattr(c, "split", None) is None and not (isinstance(c, dict) and c.get("split")))
    weighted = sum(1 for c in cases if split_of(c) == "representative"
                   and (getattr(c, "weight", None) if not isinstance(c, dict) else c.get("weight")) is not None)
    by_group = {g: {"present": [t for t in present if TAGS[t][0] == g], "missing": [t for t in missing if TAGS[t][0] == g]}
                for g in GROUPS}
    problems = []
    if untagged:
        problems.append(f"{untagged} of {n} cases have no difficulty tag (typical / edge / adversarial) or split")
    if rep < min_representative:
        problems.append(f"representative set too small ({rep} < {min_representative}): sample it from production, since "
                        "its score is the production estimate")
    if not stress:
        problems.append("no stress cases (edge / adversarial)")
    if missing:
        problems.append("stress suite has no cases for: " + ", ".join(missing))
    return {"workflow": WORKFLOW_ALIASES.get(str(workflow or ""), "single_turn"), "cases": n, "counts": dict(counts),
            "present": present, "missing": missing, "representative": rep, "stress": stress,
            "representative_weighted": weighted, "stress_share": (stress / n) if n else 0.0, "untagged": untagged,
            "by_group": by_group, "problems": problems}


def mixed_gates(suite: Any) -> list[str]:
    """Blocking gates that average representative and stress cases together: a mean / CI / pass rate over a grader
    without split= or tag=, with a threshold below 1.0. (A requirement that every case passes, `>= 1.0`, is not an
    average and applies to both sets.)"""
    import ast
    out = []
    for g in getattr(suite, "gates", []) or []:
        if getattr(g, "severity", "block") != "block":
            continue
        try:
            tree = ast.parse(g.rule, mode="eval")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for call in [node.left, *node.comparators]:
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in ("mean", "ci_low", "ci_high", "pass_rate", "pass_at_k", "pass_hat_k")):
                    continue
                if any(k.arg in ("split", "tag") for k in call.keywords):
                    continue
                if call.args and isinstance(call.args[0], ast.Name) and call.args[0].id in (
                        "latency_ms", "ttft_ms", "cost_usd", "tokens", "steps"):
                    continue
                consts = [c.value for c in [node.left, *node.comparators] if isinstance(c, ast.Constant)]
                if consts and all(isinstance(v, (int, float)) and v >= 1.0 for v in consts):
                    continue
                out.append(g.name)
                break
    return sorted(set(out))


def describe(workflow: str | None = None) -> list[dict[str, str]]:
    tags = applicable(workflow) if workflow else list(TAGS)
    return [{"tag": t, "group": TAGS[t][0], "description": TAGS[t][1]} for t in tags]
