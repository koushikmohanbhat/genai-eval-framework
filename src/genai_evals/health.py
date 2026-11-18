"""Task health: is each case a fair, solvable test, and is the suite still informative?

Three checks, following Anthropic's guidance on agent evals ("Demystifying evals for AI agents"):

- **Reference check** (`check_references`): grade each case's *reference* as if it were the system's output. A
  known-good answer that a grader rejects means the task or the grader is broken (ambiguous spec, brittle matching,
  wrong threshold), not that the agent is weak.
- **Never and always passing** (`result_health`): a case that fails every trial on every target usually signals a
  broken task or grader; a grader that every regression case passes on every trial is *saturated* and can only show
  regressions, not progress.
- **Graduation** (`graduation`): capability cases that pass every trial across several runs are ready to move to the
  regression set.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from .models import Case, Sample, Score, ToolCall

#: graders that measure the run, not the answer: they can't be checked against a reference
OPERATIONAL = {"latency", "cost", "token_budget"}


def reference_sample(case: Case, target: str = "reference") -> Sample | None:
    """A Sample whose output is the case's reference: answer / output text, extracted fields, expected final state and
    expected tool calls. None when the reference holds nothing that could stand in for an output."""
    ref = case.reference or {}
    s = Sample(case_id=case.id, target=target, input=case.input, origin="reference")
    found = False
    for k in ("output", "answer", "expected"):
        v = ref.get(k)
        if isinstance(v, str) and v.strip():
            s.output, found = v, True
            break
    if isinstance(ref.get("fields"), dict):
        s.output_json = ref["fields"]
        s.output = s.output or json.dumps(ref["fields"], ensure_ascii=False)
        found = True
    elif isinstance(ref.get("output_json"), (dict, list)):
        s.output_json = ref["output_json"]
        s.output = s.output or json.dumps(ref["output_json"], ensure_ascii=False)
        found = True
    if isinstance(ref.get("final_state"), dict):
        s.final_state, found = dict(ref["final_state"]), True
    calls = [t for t in ref.get("tool_calls") or [] if isinstance(t, dict) and t.get("name")]
    seq = [str(x) for x in ref.get("tool_sequence") or []]
    if calls or seq:
        # the expected order (tool_sequence) with the expected arguments (tool_calls) filled in by name
        pending = list(calls)
        traj = []
        for name in seq:
            hit = next((c for c in pending if c["name"] == name), None)
            if hit:
                pending.remove(hit)
            traj.append(ToolCall(name=name, args=(hit or {}).get("args") or {}))
        traj += [ToolCall(name=c["name"], args=c.get("args") or {}) for c in pending]
        s.trajectory, s.steps, found = traj, len(traj), True
    gold = [str(g) for g in ref.get("gold_passages") or []]
    if gold:
        from .models import ContextChunk
        s.context = [ContextChunk(id=g, text=str(ref.get("answer") or "")) for g in gold]
        if s.output:
            s.output = s.output + " " + " ".join(f"[{g}]" for g in gold)
    if ref.get("should_refuse") is True or ref.get("answerable") is False:
        s.output = s.output or "I'm sorry, I can't help with that: it isn't covered in the documents."
        found = True
    if found and s.output is None:
        s.output = ""
    return s if found else None


async def _check_references(suite: Any, providers: Any, judges: bool, graders: list[str] | None) -> dict[str, Any]:
    from .config import load_cases
    from .graders import GradeContext, build_grader
    from .providers import ProviderPool
    cases = load_cases(suite)
    samples = {c.id: reference_sample(c) for c in cases}
    specs = [g for g in suite.graders if g.type not in OPERATIONAL and (not graders or g.name in graders)]
    pool = ProviderPool(providers)
    gctx = GradeContext(suite=suite, pool=pool, providers=providers, target_families={},
                        samples={("reference", c.id, 0): s for c, s in ((c, samples[c.id]) for c in cases) if s},
                        tokenize=lambda x: x, calibration={})
    built = []
    for g in specs:
        gr = build_grader(g, gctx)
        if getattr(gr, "uses_model", False) and not judges:
            continue
        built.append(gr)
    rejected: list[dict[str, Any]] = []
    unchecked: dict[str, list[str]] = defaultdict(list)
    no_reference = [c.id for c in cases if samples[c.id] is None]
    checked = 0

    async def one(gr, case: Case, s: Sample) -> None:
        nonlocal checked
        if not gr.applies(case):
            return
        try:
            sc: Score = await gr.grade(s, case)
        except Exception as e:  # noqa: BLE001
            rejected.append({"case_id": case.id, "grader": gr.name, "why": f"grader raised {type(e).__name__}: {e}"})
            return
        if sc.skipped:
            unchecked[gr.name].append(case.id)
        elif sc.error or sc.passed is False:
            rejected.append({"case_id": case.id, "grader": gr.name, "value": sc.value,
                             "why": sc.error or (sc.reasoning or "")[:300] or f"value {sc.value}"})
        else:
            checked += 1
    await asyncio.gather(*[one(gr, c, samples[c.id]) for c in cases if samples[c.id] for gr in built])
    return {"cases": len(cases), "graders": [g.name for g in built], "checked": checked,
            "rejected": sorted(rejected, key=lambda r: (r["case_id"], r["grader"])),
            "no_reference": no_reference, "not_checkable": {g: len(v) for g, v in unchecked.items()},
            "judges_included": judges}


def check_references(suite: Any, providers: Any, judges: bool = False, graders: list[str] | None = None) -> dict[str, Any]:
    """Grade every case's reference as if it were the output. `rejected` lists (case, grader) pairs where a known-good
    answer fails: fix the task (say what is checked) or the grader (too strict, wrong key, wrong threshold). Graders
    that need data a reference can't supply (retrieved context, latency) are counted in `not_checkable`. Model graders
    run only with judges=True (they cost money)."""
    return asyncio.run(_check_references(suite, providers, judges, graders))


def result_health(result: dict[str, Any], min_cases: int = 5) -> dict[str, Any]:
    """Cases that never pass (every trial, every target, value 0): likely broken task or grader. Graders that every
    regression case passes on every trial: saturated. Also lists capability cases ready to graduate."""
    summ = result.get("summary") or {}
    never: dict[str, list[str]] = defaultdict(list)        # grader -> cases
    per_g_vals: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    per_g_pass: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for t in summ.values():
        for g, d in (t.get("graders") or {}).items():
            for cid, v in (d.get("per_case") or {}).items():
                per_g_vals[g][cid].append(v)
            for cid, ok in (d.get("per_case_pass") or {}).items():
                per_g_pass[g][cid].append(bool(ok))
    for g, cases in per_g_vals.items():
        for cid, vs in cases.items():
            if vs and all(v == 0 for v in vs):
                never[g].append(cid)
    saturated = []
    for g, cases in per_g_pass.items():
        if len(cases) >= min_cases and all(all(oks) for oks in cases.values()):
            saturated.append(g)
    ready = sorted({c for t in summ.values() for c in ((t.get("capability") or {}).get("ready_to_graduate") or [])})
    notes = []
    for g, cs in sorted(never.items()):
        notes.append(f"{g}: {len(cs)} case(s) never passed on any trial or target ({', '.join(sorted(cs)[:5])}"
                     f"{', ...' if len(cs) > 5 else ''}): check the task and the grader before blaming the system "
                     "(genai-evals cases check-refs)")
    if saturated:
        notes.append(f"saturated: every regression case passes every trial on {', '.join(sorted(saturated))}; these "
                     "graders can only catch regressions now. Add harder cases (or capability cases) to measure progress")
    if ready:
        notes.append(f"{len(ready)} capability case(s) passed every trial: candidates to graduate to regression "
                     "(genai-evals cases graduate)")
    return {"never_pass": {g: sorted(v) for g, v in never.items()}, "saturated": sorted(saturated),
            "ready_to_graduate": ready, "notes": notes}


def graduation(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Capability cases that passed every trial of every grader on every target in **all** the given runs."""
    seen: dict[str, list[bool]] = defaultdict(list)
    runs = 0
    for r in results:
        runs += 1
        ids_this_run: dict[str, list[bool]] = defaultdict(list)
        for t in (r.get("summary") or {}).values():
            for d in ((t.get("capability") or {}).get("graders") or {}).values():
                for cid, ok in (d.get("per_case_pass") or {}).items():
                    ids_this_run[cid].append(bool(ok))
        for cid, oks in ids_this_run.items():
            seen[cid].append(bool(oks) and all(oks))
    ready = sorted(c for c, runs_ok in seen.items() if len(runs_ok) == runs and all(runs_ok))
    return {"runs": runs, "capability_cases": len(seen), "ready": ready,
            "not_ready": sorted(c for c in seen if c not in ready)}


def graduate_dataset(path: str, ids: list[str]) -> int:
    """Set purpose: regression on the given ids in a JSONL dataset (in place). Returns how many rows changed."""
    from pathlib import Path
    p = Path(path)
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for r in rows:
        if r.get("id") in ids and r.get("purpose") == "capability":
            r["purpose"] = "regression"
            changed += 1
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return changed
