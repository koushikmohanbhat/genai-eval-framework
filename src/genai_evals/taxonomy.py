"""Standard case tags for coverage, following the edge cases in OpenAI's evaluation best practices.

Tag cases with these (alongside your own domain tags) so a suite can be checked for what it does not yet test, and so
results can be read per slice (`summary.slices` already breaks every grader down by tag). `genai-evals doctor` and
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


def coverage(cases: list[Any], workflow: str | None = None, min_per_tag: int = 1,
             min_hard_share: float = 0.2) -> dict[str, Any]:
    """Which standard tags the cases carry, which applicable ones are missing, and the share of edge / adversarial
    cases. Cases may be Case objects or dicts with `tags`."""
    def tags_of(c):
        return list(getattr(c, "tags", None) or (c.get("tags") if isinstance(c, dict) else None) or [])
    counts = Counter(t for c in cases for t in set(tags_of(c)))
    app = applicable(workflow)
    present = {t: counts[t] for t in app if counts[t] >= min_per_tag}
    missing = [t for t in app if counts[t] < min_per_tag]
    n = len(cases)
    hard = sum(1 for c in cases if {"edge", "adversarial"} & set(tags_of(c)))
    untagged = sum(1 for c in cases if not {"typical", "edge", "adversarial"} & set(tags_of(c)))
    by_group = {g: {"present": [t for t in present if TAGS[t][0] == g], "missing": [t for t in missing if TAGS[t][0] == g]}
                for g in GROUPS}
    problems = []
    if untagged:
        problems.append(f"{untagged} of {n} cases have no difficulty tag (typical / edge / adversarial)")
    if n and hard / n < min_hard_share:
        problems.append(f"only {hard / n:.0%} of cases are edge or adversarial (aim for at least {min_hard_share:.0%})")
    if missing:
        problems.append("no cases for: " + ", ".join(missing))
    return {"workflow": WORKFLOW_ALIASES.get(str(workflow or ""), "single_turn"), "cases": n, "counts": dict(counts),
            "present": present, "missing": missing, "hard_share": (hard / n) if n else 0.0, "untagged": untagged,
            "by_group": by_group, "problems": problems}


def describe(workflow: str | None = None) -> list[dict[str, str]]:
    tags = applicable(workflow) if workflow else list(TAGS)
    return [{"tag": t, "group": TAGS[t][0], "description": TAGS[t][1]} for t in tags]
