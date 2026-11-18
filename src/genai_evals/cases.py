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

    by_tag=False: a simple random sample. by_tag=True: proportional-allocation stratified sample by the first tag,
    with each case weighted by stratum size / cases drawn, so the weighted mean estimates the whole population.

    Returns (draft cases, provenance). The provenance (source, population, window, method, weights, drawn_at, plus the
    telemetry sampling that produced the result) goes into the suite's `representative_sample:`."""
    import datetime as _dt
    import random as _random
    rng = _random.Random(seed)
    cases = {c["id"]: c for c in result.get("cases", [])}
    units: dict[tuple[str, str], dict] = {}
    for smp in result.get("samples", []):
        units.setdefault((smp["case_id"], smp["target"]), smp)          # one sample per unit (first repeat)
    keys = sorted(units)
    if not keys:
        raise ValueError("no samples in this result")
    n = min(n, len(keys))
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
            picked.append((k, st, len(ks) / take))
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)
    srcs = result.get("sources") or []
    def _w(w):
        return ", ".join(f"{k} {v}" for k, v in w.items()) if isinstance(w, dict) else str(w)
    window = "; ".join(f"{s.get('source')}: {_w(s.get('window'))}" for s in srcs if s.get("window")) or result.get("created_at", "")
    fixture = any(s.get("fixture") for s in srcs)
    out = []
    for (cid, tgt), st, w in picked:
        smp = units[(cid, tgt)]
        inp = smp.get("input")
        text = tok(inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False))
        row = {"id": _id(prefix, result.get("run_id"), cid, tgt), "input": text,
               "tags": ["typical", "sampled"], "critical": False, "source": "synthetic" if fixture else "production",
               "split": "representative", "review_status": "draft", "reference": {},
               "metadata": {"sampled_from": result.get("run_id"), "original_case_id": cid, "target": tgt,
                            "trace_id": smp.get("trace_id"), "session_id": smp.get("session_id"), "stratum": st}}
        if by_tag:
            row["weight"] = round(w, 6)
        out.append(row)
    prov = {"source": "fixture (not production: recorded telemetry)" if fixture else "production",
            "population": population or ", ".join(sorted({s.get("source", "?") for s in srcs})) or "samples in " + str(result.get("run_id")),
            "window": window,
            "method": (f"stratified random sample by first tag, proportional allocation, {len(out)} of {total} units"
                       if by_tag else f"simple random sample of {len(out)} of {total} units (sessions / cases), seed {seed}"),
            "weights": "inverse inclusion: units in the tag stratum / units drawn from it" if by_tag else None,
            "drawn_at": _dt.date.today().isoformat(), "sampled_from_run": result.get("run_id"),
            "telemetry_sampling": [{k: s.get(k) for k in ("source", "type", "sampling", "sampling_method", "population",
                                                           "samples", "window") if s.get(k) is not None} for s in srcs]}
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
            allow_empty_reference: bool = False, schema: str | Path | None = None) -> tuple[list[str], list[str]]:
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
        if not {"typical", "edge", "adversarial"} & set(d.get("tags") or []) and d.get("split") != "representative":
            d["tags"] = list(d.get("tags") or []) + ["edge"]
        d["review_status"] = "reviewed"
        meta = dict(d.get("metadata") or {})
        meta.pop("output", None)                       # the failing output was review context, not test data
        if reviewer:
            meta["reviewed_by"] = reviewer
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


def default_tokenizer() -> Any:
    from .dpdp import BuiltinTokenizer
    return BuiltinTokenizer(os.environ.get("GENAI_EVALS_TOKEN_KEY"))
