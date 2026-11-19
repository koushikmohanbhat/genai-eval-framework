"""Growing the eval set: coverage against the standard tags, draft cases generated for missing tags, and draft cases
harvested from production results, with expert review before any of them counts.

OpenAI's evaluation best practices: use a strong model to help generate diverse scenarios and have experts label
them; log production traffic and keep adding cases to the eval set. Both paths here write **draft** cases
(`review_status: draft`), which `load_cases` leaves out of runs and gates until an expert has reviewed them and
`genai-evals cases promote` has marked them `reviewed`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import taxonomy

GENERATE_PROMPT = """<task>generate_cases</task>
You write test cases for evaluating an AI system. Write {n} new, realistic user inputs for the coverage category below.
Make them varied (different wording, length and intent), plausible for this system, and different from the examples.
Do not include real people's personal data; use obviously fictional names and numbers.
<system>
{system}
</system>
<category tag="{tag}">
{description}
</category>
<examples>
{examples}
</examples>
For each case, explain in "why" what makes it a good test of the category, then give the input and what a correct
response must do.
Respond with JSON only: {{"cases": [{{"why": "...", "input": "...", "expected": "..."}}]}}"""


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(rows: list[dict[str, Any]], path: str | Path, append: bool = False) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _id(prefix: str, *parts: Any) -> str:
    return f"{prefix}{hashlib.sha256(':'.join(map(str, parts)).encode()).hexdigest()[:8]}"


# ---- harvest -------------------------------------------------------------------------------------------------------

def harvest(result: dict[str, Any], graders: list[str] | None = None, below: float | None = None, limit: int = 50,
            tokenizer: Any = None, prefix: str = "prod-") -> list[dict[str, Any]]:
    """Draft cases from the samples in a result (usually an online run on production traffic) that failed, or scored
    below `below` on, any of `graders` (default: every grader). Inputs and outputs are tokenised again with
    `tokenizer` (idempotent on text that is already tokenised). Duplicate inputs are kept once. The draft has no
    reference: the reviewer writes it."""
    bad: dict[tuple[str, str], list[str]] = {}
    for s in result.get("scores", []):
        if s.get("skipped") or (graders and s["grader"] not in graders):
            continue
        failed = s.get("passed") is False or bool(s.get("error")) or (below is not None and s.get("value") is not None
                                                                         and s["value"] < below)
        if failed:
            bad.setdefault((s["case_id"], s["target"]), [])
            if s["grader"] not in bad[(s["case_id"], s["target"])]:
                bad[(s["case_id"], s["target"])].append(s["grader"])
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)
    out, seen = [], set()
    for smp in result.get("samples", []):
        key = (smp["case_id"], smp["target"])
        if key not in bad:
            continue
        inp = smp.get("input")
        text = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False)
        text = tok(text)
        if text in seen:
            continue
        seen.add(text)
        out.append({
            "id": _id(prefix, result.get("run_id"), smp["case_id"], text), "input": text,
            "tags": ["harvested"] + [f"failed:{g}" for g in bad[key]], "critical": False, "source": "production",
            "review_status": "draft", "reference": {},
            "metadata": {"harvested_from": result.get("run_id"), "original_case_id": smp["case_id"],
                         "target": smp["target"], "trace_id": smp.get("trace_id"), "session_id": smp.get("session_id"),
                         "failed_graders": bad[key], "output": tok(smp.get("output") or "")[:2000]}})
        if len(out) >= limit:
            break
    return out


# ---- sample (the representative set) -----------------------------------------------------------------------------

def sample_representative(result: dict[str, Any], n: int = 50, seed: int = 7, by_tag: bool = False,
                          tokenizer: Any = None, prefix: str = "rep-", population: str | None = None
                          ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Draft representative cases from the samples in a result, usually an online run over production telemetry.
    Selection is by the unit (session / case and target), **not** by score, so the set reflects traffic rather than
    failures.

    Each case's weight is the inverse of its probability of being included, end to end:

        P(in set) = capture_rate (telemetry) x sampling (online run) x selection here (n / N, per stratum)

    so sources captured or graded at different rates are weighted back to production proportions. by_tag=False is a
    simple random sample of the captured units; by_tag=True is a proportional stratified sample by first tag.

    When any source's telemetry capture rate is unknown (`capture_rate` not set in sources.yaml), the weights can only
    correct for the online sampling and the selection here, and the provenance says the set **represents the captured
    sample, not production traffic**.

    Returns (draft cases, provenance) for the suite's `representative_sample:`."""
    import datetime as _dt
    import random as _random
    rng = _random.Random(seed)
    cases = {c["id"]: c for c in result.get("cases", [])}
    srcs = result.get("sources") or []
    by_name = {s.get("source"): s for s in srcs}
    units: dict[tuple[str, str], dict] = {}
    for smp in result.get("samples", []):
        units.setdefault((smp["case_id"], smp["target"]), smp)          # one sample per unit (first repeat)
    keys = sorted(units)
    if not keys:
        raise ValueError("no samples in this result")
    n = min(n, len(keys))

    def source_of(smp) -> dict:
        origin = str(smp.get("origin") or "")
        name = origin.split(":", 1)[1] if origin.startswith("source:") else None
        return by_name.get(name) or (srcs[0] if len(srcs) == 1 else {})
    strata: dict[str, list] = {}
    for k in keys:
        tags = cases.get(k[0], {}).get("tags") or ["(untagged)"]
        strata.setdefault(sorted(tags)[0] if by_tag else "all", []).append(k)
    picked: list[tuple[tuple[str, str], str, float]] = []
    total = len(keys)
    for st, ks in sorted(strata.items()):
        take = max(1, round(n * len(ks) / total)) if by_tag else n
        take = min(take, len(ks))
        for k in rng.sample(ks, take):
            picked.append((k, st, take / len(ks)))
    used = {}
    unknown_capture = False
    out = []
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)
    for (cid, tgt), st, p_select in picked:
        smp = units[(cid, tgt)]
        src = source_of(smp)
        used[src.get("source", "?")] = src
        p_online = float(src.get("sampling") or 1.0)
        cap = src.get("capture_rate")
        if cap is None:
            unknown_capture = True
        p = p_select * p_online * (float(cap) if cap is not None else 1.0)
        inp = smp.get("input")
        text = tok(inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False))
        row = {"id": _id(prefix, result.get("run_id"), cid, tgt), "input": text,
               "tags": ["typical", "sampled"], "critical": False,
               "source": "synthetic" if src.get("fixture") else "production",
               "split": "representative", "review_status": "draft", "reference": {}, "weight": round(1 / p, 6),
               "metadata": {"sampled_from": result.get("run_id"), "original_case_id": cid, "target": tgt,
                            "telemetry_source": src.get("source"), "trace_id": smp.get("trace_id"),
                            "session_id": smp.get("session_id"), "stratum": st,
                            "inclusion": {"capture_rate": cap, "online_sampling": p_online, "selection": round(p_select, 6)}}}
        out.append(row)
    fixture = any(s.get("fixture") for s in used.values())
    rates = {(s.get("capture_rate"), s.get("sampling")) for s in used.values()}
    # constant weights carry no information: drop them unless rates differ or the sample was stratified
    if not by_tag and len(rates) <= 1:
        for r in out:
            r.pop("weight")

    def _w(w):
        return ", ".join(f"{k} {v}" for k, v in w.items()) if isinstance(w, dict) else str(w)
    window = "; ".join(f"{s.get('source')}: {_w(s.get('window'))}" for s in used.values() if s.get("window")) or result.get("created_at", "")
    if not srcs:
        represents = f"the cases in run {result.get('run_id')} only (no telemetry source recorded)"
    elif unknown_capture:
        represents = ("the captured telemetry sample only: the telemetry capture rate of "
                      + ", ".join(sorted(k for k, s in used.items() if s.get("capture_rate") is None))
                      + " is unknown (set capture_rate in sources.yaml)")
    else:
        represents = "production traffic"
    weights_desc = None
    if any("weight" in r for r in out):
        weights_desc = ("inverse inclusion probability: 1 / (telemetry capture rate x online sampling x selection"
                        + (" within tag stratum" if by_tag else "") + ")"
                        + ("; capture rate unknown for some sources, so treated as 1" if unknown_capture else ""))
    prov = {"source": "fixture (not production: recorded telemetry)" if fixture else "production",
            "population": population or ", ".join(sorted(used)) or "samples in " + str(result.get("run_id")),
            "window": window,
            "method": (f"stratified random sample by first tag, proportional allocation, {len(out)} of {total} captured units"
                       if by_tag else f"simple random sample of {len(out)} of {total} captured units (sessions / cases), seed {seed}"),
            "weights": weights_desc, "represents": represents,
            "drawn_at": _dt.date.today().isoformat(), "sampled_from_run": result.get("run_id"),
            "telemetry_sampling": [{k: s.get(k) for k in ("source", "type", "capture_rate", "sampling", "inclusion_probability",
                                                           "sampling_method", "population", "samples", "window")
                                    if s.get(k) is not None} for s in used.values()]}
    return out, prov


# ---- generate ------------------------------------------------------------------------------------------------------

async def _generate(provider: Any, prompt: str) -> list[dict[str, Any]]:
    from .models import Message
    from .targets.base import parse_json
    r = await provider.complete([Message(role="user", content=prompt)], temperature=0.7, json_mode=True)
    data = parse_json(r.text)
    return list((data or {}).get("cases") or []) if isinstance(data, dict) else []


def generate(suite: Any, providers: Any, generator: str, tags: list[str] | None = None, n: int = 5,
             workflow: str | None = None, system: str | None = None, existing: list[Any] | None = None,
             tokenizer: Any = None) -> list[dict[str, Any]]:
    """Draft cases for each tag (default: the applicable standard tags the dataset is missing), written by the
    `generator` provider profile (use a strong model). Each draft carries the tag, `generated`, the generator's note
    on what a correct response must do (metadata.expected), and no reference: an expert reviews it and writes one."""
    from .providers import ProviderPool
    existing = existing or []
    if not tags:
        tags = taxonomy.coverage(existing, workflow)["missing"]
    unknown = [t for t in tags if t not in taxonomy.TAGS]
    if unknown:
        raise ValueError(f"unknown coverage tags: {', '.join(unknown)} (see `genai-evals cases tags`)")
    pool = ProviderPool(providers)
    prov = pool.get(generator)
    sys_text = system or suite.description or suite.name
    ex = "\n".join(f"- {c.input_text() if hasattr(c, 'input_text') else c.get('input')}" for c in existing[:5]) or "(none)"
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)

    async def all_tags():
        return await asyncio.gather(*[_generate(prov, GENERATE_PROMPT.format(
            n=n, system=sys_text, tag=t, description=taxonomy.TAGS[t][1], examples=ex)) for t in tags])
    out = []
    for t, rows in zip(tags, asyncio.run(all_tags())):
        for r in rows[:n]:
            if not isinstance(r, dict) or not str(r.get("input") or "").strip():
                continue
            text = tok(str(r["input"]).strip())
            out.append({"id": _id("gen-", t, text), "input": text, "tags": [t, "generated"], "critical": False,
                        "source": "synthetic", "review_status": "draft", "reference": {},
                        "metadata": {"generator": generator, "generator_model": prov.profile.model,
                                     "expected": r.get("expected"), "why": r.get("why")}})
    return out


# ---- promote -------------------------------------------------------------------------------------------------------

def promote(drafts_path: str | Path, into: str | Path, ids: list[str] | None = None, reviewer: str | None = None,
            allow_empty_reference: bool = False, schema: str | Path | None = None,
            reviewers: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Move reviewed drafts into a dataset: mark them `review_status: reviewed` (with the reviewer), drop the
    `harvested` / `generated` working tags' draft-only metadata, and append them. Refuses drafts with an empty
    reference unless allow_empty_reference (a reference-free grader suite). Returns (promoted ids, refused messages).
    Drafts not promoted stay in the drafts file."""
    drafts = read_jsonl(drafts_path)
    target = read_jsonl(into)
    have = {r["id"] for r in target}
    pick = set(ids) if ids else {d["id"] for d in drafts}
    promoted, refused, keep = [], [], []
    for d in drafts:
        if d["id"] not in pick:
            keep.append(d)
            continue
        if d["id"] in have:
            refused.append(f"{d['id']}: already in {Path(into).name}")
            continue
        if not d.get("reference") and not allow_empty_reference:
            refused.append(f"{d['id']}: no reference yet (write one, or pass --allow-empty-reference)")
            keep.append(d)
            continue
        rv = (reviewers or {}).get(d["id"]) or reviewer
        meta0 = d.get("metadata") or {}
        if "seeded" in (d.get("tags") or []):
            # seeded cases were written by a model from untrusted documents: a person must sign each one off
            if not rv:
                refused.append(f"{d['id']}: seeded drafts need a reviewer (--sheet, or --reviewer after reviewing them)")
                keep.append(d)
                continue
            if meta0.get("flags") and d["id"] not in (reviewers or {}):
                refused.append(f"{d['id']}: flagged ({', '.join(meta0['flags'])}): decide it on the review sheet (--sheet)")
                keep.append(d)
                continue
        if not {"typical", "edge", "adversarial"} & set(d.get("tags") or []) and d.get("split") != "representative":
            d["tags"] = list(d.get("tags") or []) + ["edge"]
        d["review_status"] = "reviewed"
        meta = dict(d.get("metadata") or {})
        meta.pop("output", None)                       # the failing output was review context, not test data
        for k in ("evidence", "passage_excerpt", "closest_passages", "flags", "source_check"):   # review context
            meta.pop(k, None)
        if rv:
            meta["reviewed_by"] = rv
        d["metadata"] = meta
        promoted.append(d["id"])
        target.append(d)
    if schema and promoted:
        from .config import check_rows
        check_rows([d for d in target if d["id"] in promoted], Path(schema))
    if promoted:
        write_jsonl(target, into)
        write_jsonl(keep, drafts_path)
    return promoted, refused


# ---- review sheet for drafts --------------------------------------------------------------------------------------

REVIEW_COLUMNS = ["id", "kind", "flags", "tags", "input", "draft_answer", "answerable", "gold_passages", "source_location",
                  "evidence", "passage_excerpt", "closest_passages", "source_check", "decision", "corrected_input", "corrected_answer",
                  "corrected_answerable", "reviewer", "notes"]
REVIEW_EDITABLE = ["decision", "corrected_input", "corrected_answer", "corrected_answerable", "reviewer", "notes"]


def write_review_sheet(drafts_path: str | Path, out: str | Path) -> int:
    """A CSV an expert fills in to sign drafts off: one row per draft with the draft answer, its source passage and
    evidence. decision: keep (promote as drafted), fix (promote with the corrected_* columns), drop (delete), or
    blank (leave as a draft)."""
    from .sheets import write_rows
    rows = read_jsonl(drafts_path)
    out_rows: list[dict[str, Any]] = []
    for d in rows:
        ref, meta = d.get("reference") or {}, d.get("metadata") or {}
        ans = ref.get("answerable")
        loc = meta.get("source_location")
        locs = loc if isinstance(loc, list) else ([loc] if loc else [])
        sc = meta.get("source_check") or {}
        out_rows.append({"id": d["id"], "kind": meta.get("kind", ""), "flags": ";".join(meta.get("flags") or []),
                    "source_location": " | ".join(", ".join(f"{k} {v}" for k, v in x.items() if v not in (None, ""))
                                                  for x in locs if x),
                    "source_check": (f"generator gave passage '{sc.get('generator_passage_id') or '(none)'}', which "
                                     f"doesn't exist; {sc.get('assigned')} was assigned by word overlap: check it")
                                    if sc else "",
                    "tags": ";".join(d.get("tags") or []),
                    "input": d.get("input") if isinstance(d.get("input"), str) else json.dumps(d.get("input")),
                    "draft_answer": ref.get("answer", ""), "answerable": "" if ans is None else ("yes" if ans else "no"),
                    "gold_passages": ";".join(ref.get("gold_passages") or []), "evidence": meta.get("evidence", ""),
                    "passage_excerpt": meta.get("passage_excerpt", ""),
                    "closest_passages": ";".join(meta.get("closest_passages") or []), "decision": "",
                    "corrected_input": "", "corrected_answer": "", "corrected_answerable": "", "reviewer": "",
                    "notes": ""})
    write_rows(out, REVIEW_COLUMNS, out_rows, editable=REVIEW_EDITABLE, choices={"decision": ["keep", "fix", "drop"],
                                                                                    "corrected_answerable": ["yes", "no"]})
    return len(rows)


def apply_review_sheet(drafts_path: str | Path, sheet: str | Path, reviewer: str | None = None) -> dict[str, Any]:
    """Apply an expert's decisions to the drafts file: fixes are written into the drafts, drops removed. Returns the
    ids to promote (keep and fix), with the reviewer per id. A decided row needs a reviewer (the column, or the
    `reviewer` argument): that's the sign-off."""
    from .sheets import read_rows
    drafts = {d["id"]: d for d in read_jsonl(drafts_path)}
    keep, fixed, dropped, problems, who = [], [], [], [], {}
    for r in read_rows(sheet):
        did, dec = (r.get("id") or "").strip(), (r.get("decision") or "").strip().lower()
        if not dec:
            continue
        if did not in drafts:
            problems.append(f"{did}: not in {Path(drafts_path).name}")
            continue
        rv = (r.get("reviewer") or "").strip() or reviewer
        if not rv:
            problems.append(f"{did}: decided ({dec}) but no reviewer signed it off")
            continue
        if dec == "drop":
            dropped.append(did)
            del drafts[did]
            continue
        if dec not in ("keep", "fix"):
            problems.append(f"{did}: decision must be keep, fix or drop (got {dec})")
            continue
        d = drafts[did]
        ref = dict(d.get("reference") or {})
        if dec == "fix":
            if (r.get("corrected_input") or "").strip():
                d["input"] = r["corrected_input"].strip()
            ca = (r.get("corrected_answerable") or "").strip().lower()
            if ca in ("yes", "no"):
                ref["answerable"] = ca == "yes"
                if ca == "no":
                    ref.pop("answer", None)
                    ref.pop("gold_passages", None)
                    d["tags"] = [t for t in d.get("tags") or [] if t != "answerable"] + ["out-of-scope", "no_answer"]
                elif "answerable" not in (d.get("tags") or []):
                    d["tags"] = ["answerable"] + [t for t in d.get("tags") or [] if t not in ("out-of-scope", "no_answer")]
            if (r.get("corrected_answer") or "").strip():
                ref["answer"] = r["corrected_answer"].strip()
            fixed.append(did)
        else:
            keep.append(did)
        notes = (r.get("notes") or "").strip()
        meta = dict(d.get("metadata") or {})
        if notes:
            meta["review_notes"] = notes
        d["metadata"] = meta
        d["reference"] = ref
        who[did] = rv
    write_jsonl(list(drafts.values()), drafts_path)
    return {"promote": keep + fixed, "kept": keep, "fixed": fixed, "dropped": dropped, "problems": problems,
            "reviewers": who}


def default_tokenizer() -> Any:
    from .dpdp import BuiltinTokenizer
    return BuiltinTokenizer(os.environ.get("GENAI_EVALS_TOKEN_KEY"))
