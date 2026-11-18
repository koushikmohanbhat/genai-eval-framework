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
# risk tier per coverage tag: a high-risk tag needs more cases before its slice supports a conclusion
HIGH_RISK = {"jailbreak", "system_conflict", "bad_tool_args", "circular_handoff"}


def tier_of(tag: str) -> str:
    return "high" if tag in HIGH_RISK else "standard"


def required_count(tag: str, by_tier: dict[str, int] | None = None, by_tag: dict[str, int] | None = None) -> int:
    by_tier = by_tier or {"high": 10, "standard": 5}
    return int((by_tag or {}).get(tag, by_tier.get(tier_of(tag), 5)))
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


def coverage(cases: list[Any], workflow: str | None = None, min_per_tag: int | None = None, min_representative: int = 30,
             stress_tags: list[str] | tuple[str, ...] = ("edge", "adversarial"),
             min_by_tier: dict[str, int] | None = None, min_by_tag: dict[str, int] | None = None,
             provenance: Any = None, min_production_share: float = 0.8) -> dict[str, Any]:
    """Two sets, checked separately:

    - the **representative** set (cases without a stress tag, or `split: representative`): production-like traffic.
      Its score is the production estimate, so it needs enough cases (`min_representative`) **and evidence** that it
      is representative: a provenance record (source, population, window, method, and how any weights were set) and
      cases whose `source` is production (`min_production_share`).
    - the **stress** suite: each applicable coverage tag needs enough cases for its slice to support a conclusion,
      by risk tier (default: 10 for high-risk tags such as jailbreak and system_conflict, 5 otherwise). Tags with some
      cases but fewer than that are reported as **thin**.

    Cases may be Case objects or dicts."""
    def get(c, k):
        return c.get(k) if isinstance(c, dict) else getattr(c, k, None)

    def tags_of(c):
        return list(get(c, "tags") or [])

    def split_of(c):
        sp = get(c, "split")
        if sp in ("representative", "stress"):
            return sp
        return "stress" if set(tags_of(c)) & set(stress_tags) else "representative"
    counts = Counter(t for c in cases for t in set(tags_of(c)))
    app = [t for t in applicable(workflow) if TAGS[t][0] != "difficulty"]
    need = {t: (min_per_tag if min_per_tag is not None else required_count(t, min_by_tier, min_by_tag)) for t in app}
    present = {t: counts[t] for t in app if counts[t] >= need[t]}
    thin = {t: {"cases": counts[t], "needed": need[t], "tier": tier_of(t)} for t in app if 0 < counts[t] < need[t]}
    missing = [t for t in app if counts[t] == 0]
    n = len(cases)
    reps = [c for c in cases if split_of(c) == "representative"]
    rep, stress = len(reps), n - len(reps)
    prod = sum(1 for c in reps if str(get(c, "source") or "").lower() == "production")
    weighted = sum(1 for c in reps if get(c, "weight") is not None)
    untagged = sum(1 for c in cases if not {"typical", "edge", "adversarial"} & set(tags_of(c)) and not get(c, "split"))
    prov = provenance.model_dump() if hasattr(provenance, "model_dump") else (dict(provenance) if provenance else None)
    by_group = {g: {"present": [t for t in present if TAGS[t][0] == g], "thin": [t for t in thin if TAGS[t][0] == g],
                    "missing": [t for t in missing if TAGS[t][0] == g]} for g in GROUPS if g != "difficulty"}
    problems, evidence = [], []
    if untagged:
        problems.append(f"{untagged} of {n} cases have no difficulty tag (typical / edge / adversarial) or split")
    if rep < min_representative:
        problems.append(f"representative set too small ({rep} < {min_representative}): sample it from production, since "
                        "its score is the production estimate")
    if not prov:
        problems.append("representative set has no provenance: add `representative_sample:` to the suite (source, "
                        "population, window, method), or build it with `genai-evals cases sample`")
    else:
        evidence.append(f"{prov.get('source')} sample of {prov.get('population')}, {prov.get('window')}, {prov.get('method')}")
        if weighted and not prov.get("weights"):
            problems.append(f"{weighted} representative case(s) carry weights, but the provenance doesn't say how they were set")
    if rep:
        share = prod / rep
        evidence.append(f"{prod}/{rep} representative cases have source: production")
        if share < min_production_share:
            problems.append(f"only {share:.0%} of representative cases have source: production (need "
                            f"{min_production_share:.0%}); synthetic or SME-written cases don't show what traffic looks like")
    if not stress:
        problems.append("no stress cases (edge / adversarial)")
    if missing:
        problems.append("stress suite has no cases for: " + ", ".join(missing))
    if thin:
        problems.append("thin stress coverage: " + ", ".join(f"{t} {d['cases']}/{d['needed']}" for t, d in thin.items()))
    return {"workflow": WORKFLOW_ALIASES.get(str(workflow or ""), "single_turn"), "cases": n, "counts": dict(counts),
            "required": need, "present": present, "thin": thin, "missing": missing,
            "representative": rep, "representative_production": prod, "representative_weighted": weighted,
            "provenance": prov, "evidence": evidence, "stress": stress, "stress_share": (stress / n) if n else 0.0,
            "untagged": untagged, "by_group": by_group, "problems": problems}


def thin_gates(suite: Any, cases: list[Any]) -> list[dict[str, Any]]:
    """Gates that will be invalid for lack of cases, found before a run: a tag= or split= gate whose slice has fewer
    cases than the gate's min_n (or the suite's). Reported for every severity; blocking ones are problems."""
    import ast
    stress_tags = list(getattr(suite, "stress_tags", ["edge", "adversarial"]))

    def tags_of(c):
        return set(c.get("tags") if isinstance(c, dict) else (getattr(c, "tags", None) or []))

    def split_of(c):
        sp = c.get("split") if isinstance(c, dict) else getattr(c, "split", None)
        return sp if sp in ("representative", "stress") else ("stress" if tags_of(c) & set(stress_tags) else "representative")
    out = []
    for g in getattr(suite, "gates", []) or []:
        min_n = g.min_n if g.min_n is not None else getattr(suite, "min_n", 1)
        try:
            tree = ast.parse(g.rule, mode="eval")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for k in node.keywords:
                if not isinstance(k.value, ast.Constant):
                    continue
                if k.arg == "tag":
                    have, what = sum(1 for c in cases if k.value.value in tags_of(c)), f"tag '{k.value.value}'"
                elif k.arg == "split":
                    have, what = sum(1 for c in cases if split_of(c) == k.value.value), f"{k.value.value} split"
                else:
                    continue
                if have < min_n:
                    out.append({"gate": g.name, "severity": g.severity, "slice": what, "cases": have, "min_n": min_n})
    return out


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
