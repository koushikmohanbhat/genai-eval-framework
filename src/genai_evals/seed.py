"""Day-1 dataset bootstrapper: draft an eval set from the customer's own documents.

`genai-evals cases seed --docs ./kb/ --generator <strong model profile>` reads the documents, splits them into
passages (written as a corpus the RAG pack's retriever can use), and has a generator model draft:

- **answerable** questions, each with a draft answer, a short evidence quote and the passage it comes from;
- **edge** variants of answerable questions (typos, several questions at once, very short, another language, a format
  request);
- **multi-passage** questions that need two passages, usually from different documents, to answer;
- **unanswerable** questions on the same topics that the documents don't answer (the system should say it can't).

Cost is bounded by the number of cases, not the size of the repository: passages are sampled first
(`sample_strategy`: stratified across folders and file types by default, so one big folder doesn't dominate), and
`budget_usd` stops generation before the estimated or actual spend passes it. `plan()` gives the estimate without
calling the model (`--dry-run`).

Every row is a **draft** (`review_status: draft`, `source: synthetic`): excluded from runs and gates until an expert
signs it off through the review sheet (`cases review-sheet`, then `cases promote --sheet`). Questions written from
the documents skew easy and share their wording, so seeded cases are a starting point, not the representative set:
replace them with production samples (`cases sample`) as traffic arrives. Questions and answers are tokenised (DPDP)
before they are written. The documents go to the generator profile, so use an in-region profile for customer data.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from pathlib import Path
from typing import Any

from .cases import _id

TEXT_EXT = {".txt", ".md", ".markdown", ".rst"}
HTML_EXT = {".html", ".htm"}
EDGE_KINDS = {
    "typos": "the same question with realistic typos and no punctuation",
    "multi_intent": "two related questions in one message",
    "very_short": "a terse version of two to four words",
    "multilingual": "the question in Hinglish or another language the users speak",
    "format_request": "the question plus a request for a specific format (one line, a list, a table)",
}

SEED_PROMPT = """<task>seed_cases</task>
You write test cases for evaluating a question-answering assistant over a company's documents.
Kind: {kind}. {instruction}
Write {n} case(s). Use only the passages below for answers; quote at most 20 words as evidence. Do not include real
people's personal data: if a passage has names, emails or numbers about a person, don't ask about them.
<passages>
{passages}
</passages>
Respond with JSON only: {{"cases": [{{"question": "...", "answer": "...", "evidence": "...", "passage_id": "...", "passage_ids": ["..."], "variant": "..."}}]}}"""

INSTRUCTIONS = {
    "multi_passage": "Each question must need TWO of the passages to answer (for example a rule from one and a limit or "
                     "exception from another); it must not be answerable from either alone. Give the combined answer and "
                     "the ids of both passages in passage_ids.",
    "answerable": "Each question must be answered by ONE of the passages. Give the answer in a short phrase and the id "
                  "of the passage that answers it. Ask what a real employee or customer would ask, in their words, not "
                  "by copying the passage's wording.",
    "edge": "Write a variant of a realistic question answered by one passage. variant is one of: {kinds}. Give the "
            "answer and the passage id.",
    "unanswerable": "Each question must be on the same topics as the passages, plausible for a user to ask, but NOT "
                    "answered by any of them (a related detail they don't cover). answer: what the passages are missing. "
                    "passage_id: the closest passage.",
}


# ---- reading and splitting ---------------------------------------------------------------------------------------

def read_docs(path: str | Path) -> list[tuple[str, str]]:
    """(name, text) for every supported file under path. PDF and Word need the `docs` extra."""
    root = Path(path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))
    out = []
    for f in files:
        ext = f.suffix.lower()
        rel = f.name if root.is_file() else str(f.relative_to(root))
        name = str(Path(rel).with_suffix("")) + f"::{ext.lstrip('.')}"      # folder/stem::ext: groups and ids
        if ext in TEXT_EXT:
            out.append((name, f.read_text(encoding="utf-8", errors="replace")))
        elif ext in HTML_EXT:
            raw = f.read_text(encoding="utf-8", errors="replace")
            raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
            raw = re.sub(r"(?i)<(h[1-6])[^>]*>(.*?)</\1>", lambda m: f"\n# {m.group(2)}\n", raw)
            raw = re.sub(r"(?i)</?(p|div|li|br|tr)[^>]*>", "\n", raw)
            out.append((name, html.unescape(re.sub(r"<[^>]+>", " ", raw))))
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as e:
                raise RuntimeError("reading PDFs needs pypdf: pip install 'genai-eval-framework[docs]'") from e
            out.append((name, "\n\n".join((pg.extract_text() or "") for pg in PdfReader(str(f)).pages)))
        elif ext == ".docx":
            try:
                import docx
            except ImportError as e:
                raise RuntimeError("reading Word files needs python-docx: pip install 'genai-eval-framework[docs]'") from e
            d = docx.Document(str(f))
            out.append((name, "\n\n".join((("# " if p.style.name.lower().startswith("heading") else "") + p.text)
                                            for p in d.paragraphs if p.text.strip())))
        elif ext in (".jsonl",):                       # an existing corpus: {id, title, text} per line
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    out.append((f"{r.get('id')}|{r.get('title', '')}", r.get("text", "")))
    return [(n, t) for n, t in out if t and t.strip()]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "doc"


def _doc_parts(name: str) -> tuple[str, str, str]:
    """name 'hr/leave-policy::pdf' -> (stem path 'hr/leave-policy', folder 'hr', type 'pdf')."""
    stem, _, ext = name.partition("::")
    folder = str(Path(stem).parent) if "/" in stem or "\\" in stem else "."
    return stem, folder, ext or "txt"


def _chunk_doc(name: str, text: str, words: int) -> list[dict[str, Any]]:
    stem, folder, ext = _doc_parts(name)
    base = _slug(stem)
    heading = Path(stem).name.replace("_", " ").replace("-", " ")
    groups: list[tuple[str, list[str]]] = []
    buf: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if not p:
            continue
        if re.match(r"^(#{1,6}\s+|[A-Z][A-Za-z0-9 ,&/-]{2,60}:?$)", p) and len(p.split()) <= 10:
            if buf:                                   # a heading line: start a new passage
                groups.append((heading, buf))
                buf = []
            heading = p.lstrip("# ").rstrip(":")
            continue
        if buf and sum(len(b.split()) for b in buf) + len(p.split()) > words:
            groups.append((heading, buf))
            buf = []
        buf.append(p)
    if buf:
        groups.append((heading, buf))
    return [{"id": f"{base}-{i:02d}", "title": h, "text": " ".join(" ".join(b).split()), "source": stem,
             "folder": folder, "type": ext} for i, (h, b) in enumerate(groups, 1)]


def chunk(docs: list[tuple[str, str]], words: int = 180) -> list[dict[str, Any]]:
    """Passages of about `words` words, split on paragraphs, each titled with its nearest heading."""
    out: list[dict[str, Any]] = []
    for name, text in docs:
        if "|" in name and name.split("|", 1)[0]:     # pre-chunked corpus rows keep their ids
            pid, title = name.split("|", 1)
            out.append({"id": pid, "title": title or pid, "text": " ".join(text.split()), "source": pid,
                        "folder": ".", "type": "corpus"})
        else:
            out += _chunk_doc(name, text, words)
    return out


# ---- generation ----------------------------------------------------------------------------------------------------

async def _ask(provider: Any, prompt: str) -> list[dict[str, Any]]:
    from .models import Message
    from .targets.base import parse_json
    r = await provider.complete([Message(role="user", content=prompt)], temperature=0.7, json_mode=True)
    data = parse_json(r.text)
    return list((data or {}).get("cases") or []) if isinstance(data, dict) else []


def _passages_block(ps: list[dict[str, Any]]) -> str:
    return "\n".join(f'<passage id="{p["id"]}" title="{p["title"]}">{p["text"]}</passage>' for p in ps)


def _overlap(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    return len(ta & tb) / (len(ta) or 1)


def sample_passages(passages: list[dict[str, Any]], k: int, strategy: str = "stratified", seed_: int = 7) -> list[dict[str, Any]]:
    """Pick k passages to seed from.

    - stratified (default): an equal share for every (folder, file type) group, capped at the group's size, with what's
      left spread across the bigger groups in proportion; so a 9,000-page archive doesn't crowd out the 20-page policy
      folder that matters;
    - per-doc: round-robin across documents, so every document is represented before any gets a second passage;
    - random: a uniform sample.
    """
    import random
    from collections import defaultdict
    if k >= len(passages):
        return list(passages)
    rng = random.Random(seed_)
    if strategy == "random":
        return rng.sample(passages, k)
    key = (lambda p: p.get("source")) if strategy == "per-doc" else (lambda p: (p.get("folder"), p.get("type")))
    if strategy not in ("stratified", "per-doc"):
        raise ValueError(f"sample_strategy must be stratified, per-doc or random (got {strategy})")
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for p in passages:
        groups[key(p)].append(p)
    for g in groups.values():
        rng.shuffle(g)
    if strategy == "per-doc":
        out: list[dict[str, Any]] = []
        keys = sorted(groups, key=str)
        while len(out) < k:
            for g in keys:
                if groups[g] and len(out) < k:
                    out.append(groups[g].pop())
        return out
    quota = {g: 0 for g in groups}
    left, open_ = k, sorted(groups, key=lambda g: (len(groups[g]), str(g)))
    while left > 0 and open_:                           # equal shares, smallest groups first; caps free up room
        share = max(1, left // len(open_))
        for g in list(open_):
            take = min(share, len(groups[g]) - quota[g], left)
            quota[g] += take
            left -= take
            if quota[g] >= len(groups[g]):
                open_.remove(g)
            if left <= 0:
                break
    return [p for g, q in quota.items() for p in groups[g][:q]][:k]


#: default mix of case kinds: answerable, multi-passage, edge, unanswerable
MIX = {"answerable": 0.5, "multi_passage": 0.15, "edge": 0.15, "unanswerable": 0.2}
OUT_TOKENS_PER_CASE = 160


def _jobs(passages: list[dict[str, Any]], n: int, mix: dict[str, float], seed_: int) -> list[tuple[str, list, int]]:
    import random
    rng = random.Random(seed_)
    counts = {k: int(round(n * v)) for k, v in mix.items()}
    counts["answerable"] += n - sum(counts.values())
    order = passages[:]
    rng.shuffle(order)
    batches = [order[i:i + 4] for i in range(0, len(order), 4)] or [order]
    # multi-passage batches pair passages from different documents where possible
    by_doc: dict[str, list] = {}
    for p in order:
        by_doc.setdefault(p.get("source") or p["id"], []).append(p)
    docs = list(by_doc)
    pairs = []
    for i in range(max(1, len(order) // 2)):
        if len(docs) >= 2:
            a, b = by_doc[docs[i % len(docs)]], by_doc[docs[(i + 1) % len(docs)]]
            pairs.append([a[i % len(a)], b[i % len(b)]])
        elif len(order) >= 2:
            pairs.append([order[(2 * i) % len(order)], order[(2 * i + 1) % len(order)]])
    if not pairs:
        counts["answerable"] = counts.get("answerable", 0) + counts.pop("multi_passage", 0)
    per_kind: dict[str, list] = {}
    for kind in ("answerable", "multi_passage", "edge", "unanswerable"):
        total, i = counts.get(kind, 0), 0
        pool = pairs if kind == "multi_passage" else batches
        while total > 0:
            k = min(total, 3 if kind != "multi_passage" else 2)
            per_kind.setdefault(kind, []).append((kind, pool[i % len(pool)], k))
            total -= k
            i += 1
    jobs = []                                       # interleaved, so a budget-trimmed plan keeps the mix
    while any(per_kind.values()):
        for kind in list(per_kind):
            if per_kind[kind]:
                jobs.append(per_kind[kind].pop(0))
    return jobs


def _prompt(kind: str, batch: list[dict[str, Any]], k: int) -> str:
    return SEED_PROMPT.format(kind=kind, n=k, passages=_passages_block(batch),
                              instruction=INSTRUCTIONS[kind].format(kinds=", ".join(EDGE_KINDS)))


def plan(passages: list[dict[str, Any]], providers: Any, generator: str, n: int = 40,
         mix: dict[str, float] | None = None, seed_: int = 7) -> dict[str, Any]:
    """Estimate the generator calls, tokens and cost of a seed run without calling the model (input tokens ~ characters
    / 4; output ~160 tokens per case). Cost is None when the profile has no pricing."""
    jobs = _jobs(passages, n, mix or MIX, seed_)
    tin = sum(len(_prompt(kind, b, k)) // 4 for kind, b, k in jobs)
    tout = sum(k for _, _, k in jobs) * OUT_TOKENS_PER_CASE
    prof = providers.profiles.get(generator) if hasattr(providers, "profiles") else None
    pr = getattr(prof, "pricing", None)
    cost = (tin * pr.input_per_mtok + tout * pr.output_per_mtok) / 1e6 if pr else None
    kinds: dict[str, int] = {}
    for kind, _, k in jobs:
        kinds[kind] = kinds.get(kind, 0) + k
    return {"calls": len(jobs), "cases": sum(k for _, _, k in jobs), "kinds": kinds, "input_tokens": tin,
            "output_tokens": tout, "estimated_cost_usd": cost, "priced": pr is not None}


def seed(passages: list[dict[str, Any]], providers: Any, generator: str, n: int = 40, mix: dict[str, float] | None = None,
         tokenizer: Any = None, seed_: int = 7, budget_usd: float | None = None, concurrency: int = 4,
         report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Draft cases from passages (sample them first with sample_passages). With budget_usd, the plan is trimmed to fit
    the estimate, and generation stops before actual spend (from token usage x the profile's pricing) passes it; a
    budget needs pricing on the profile. `report`, if given, receives calls, spend and whether the budget stopped it."""
    from .providers import ProviderPool
    if not passages:
        raise ValueError("no passages: nothing readable in the documents")
    mix = mix or MIX
    prof = providers.profiles.get(generator) if hasattr(providers, "profiles") else None
    if budget_usd is not None and not getattr(prof, "pricing", None):
        raise ValueError(f"a budget needs pricing on the '{generator}' profile (pricing: {{input_per_mtok, output_per_mtok}})")
    jobs = _jobs(passages, n, mix, seed_)
    if budget_usd is not None:
        pr = prof.pricing
        per_job = [((len(_prompt(kd, b, k)) // 4) * pr.input_per_mtok + k * OUT_TOKENS_PER_CASE * pr.output_per_mtok) / 1e6
                   for kd, b, k in jobs]
        keep, run = [], 0.0
        for j, c in zip(jobs, per_job):                 # interleave kinds so a trimmed plan keeps the mix
            if run + c <= budget_usd:
                keep.append(j)
                run += c
        jobs = keep
    prov = ProviderPool(providers).get(generator)
    spent, calls, stopped = 0.0, 0, False

    async def one(kind, b, k):
        from .models import Message
        from .targets.base import parse_json
        r = await prov.complete([Message(role="user", content=_prompt(kind, b, k))], temperature=0.7, json_mode=True)
        data = parse_json(r.text)
        return (list((data or {}).get("cases") or []) if isinstance(data, dict) else []), prov.cost(r.usage)

    async def run_all():
        nonlocal spent, calls, stopped
        results = []
        for i in range(0, len(jobs), max(1, concurrency)):
            if budget_usd is not None and spent >= budget_usd:
                stopped = True
                break
            wave = jobs[i:i + max(1, concurrency)]
            got = await asyncio.gather(*[one(*j) for j in wave])
            for j, (rows, c) in zip(wave, got):
                results.append((j, rows))
                spent += c or 0.0
                calls += 1
        return results
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)
    by_id = {p["id"]: p for p in passages}
    out, seen = [], set()
    for (kind, b, _), rows in asyncio.run(run_all()):
        for r in rows:
            q = str(r.get("question") or "").strip()
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            if kind == "multi_passage":
                pids = [str(x) for x in (r.get("passage_ids") or []) if str(x) in by_id]
                pids = list(dict.fromkeys(pids))
                if len(pids) < 2:                       # not a multi-passage question after all: drop it
                    continue
                srcs = [by_id[x] for x in pids]
                meta = {"seeded_from": sorted({x.get("source") for x in srcs}), "kind": kind, "generator": generator,
                        "generator_model": getattr(prov.profile, "model", None),
                        "evidence": tok(str(r.get("evidence") or ""))[:300],
                        "passage_excerpt": tok(" | ".join(x["text"][:200] for x in srcs))}
                out.append({"id": _id("seed-", kind, q), "input": tok(q), "critical": False, "source": "synthetic",
                            "tags": ["answerable", "multi_passage", "edge", "seeded"], "review_status": "draft",
                            "reference": {"answerable": True, "gold_passages": pids,
                                          "answer": tok(str(r.get("answer") or "").strip())}, "metadata": meta})
                continue
            pid = str(r.get("passage_id") or "")
            if pid not in by_id:
                pid = max(b, key=lambda p: _overlap(q, p["text"]))["id"]
            src = by_id[pid]
            meta = {"seeded_from": src.get("source"), "kind": kind, "generator": generator,
                    "generator_model": getattr(prov.profile, "model", None),
                    "evidence": tok(str(r.get("evidence") or ""))[:300], "passage_excerpt": tok(src["text"][:400])}
            if kind == "unanswerable":
                close = sorted(passages, key=lambda p: -_overlap(q, p["title"] + " " + p["text"]))[:3]
                meta["closest_passages"] = [p["id"] for p in close]      # the reviewer checks none of these answers it
                ref = {"answerable": False}
                tags = ["out-of-scope", "no_answer", "edge", "seeded"]
            else:
                ref = {"answerable": True, "gold_passages": [pid], "answer": tok(str(r.get("answer") or "").strip())}
                tags = ["answerable", "typical", "seeded"]
                if kind == "edge":
                    v = str(r.get("variant") or "").strip()
                    tags = ["answerable", v if v in EDGE_KINDS else "edge_variant", "edge", "seeded"]
            out.append({"id": _id("seed-", kind, q), "input": tok(q), "tags": tags, "critical": False,
                        "source": "synthetic", "review_status": "draft", "reference": ref, "metadata": meta})
    if report is not None:
        report.update({"calls": calls, "planned_calls": len(jobs), "spent_usd": spent if prof and prof.pricing else None,
                       "budget_usd": budget_usd, "stopped_by_budget": stopped})
    return out[:n] if len(out) > n else out
