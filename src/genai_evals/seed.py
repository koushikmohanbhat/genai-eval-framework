"""Day-1 dataset bootstrapper: draft an eval set from the customer's own documents.

`genai-evals cases seed --docs ./kb/ --generator <strong model profile>` reads the documents, splits them into
passages (written as a corpus the RAG pack's retriever can use), and has a generator model draft:

- **answerable** questions, each with a draft answer, a short evidence quote and the passage it comes from;
- **edge** variants of answerable questions (typos, several questions at once, very short, another language, a format
  request);
- **unanswerable** questions on the same topics that the documents don't answer (the system should say it can't).

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
Respond with JSON only: {{"cases": [{{"question": "...", "answer": "...", "evidence": "...", "passage_id": "...", "variant": "..."}}]}}"""

INSTRUCTIONS = {
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
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    out = []
    for f in files:
        ext = f.suffix.lower()
        if ext in TEXT_EXT:
            out.append((f.stem, f.read_text(encoding="utf-8", errors="replace")))
        elif ext in HTML_EXT:
            raw = f.read_text(encoding="utf-8", errors="replace")
            raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
            raw = re.sub(r"(?i)<(h[1-6])[^>]*>(.*?)</\1>", lambda m: f"\n# {m.group(2)}\n", raw)
            raw = re.sub(r"(?i)</?(p|div|li|br|tr)[^>]*>", "\n", raw)
            out.append((f.stem, html.unescape(re.sub(r"<[^>]+>", " ", raw))))
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as e:
                raise RuntimeError("reading PDFs needs pypdf: pip install 'genai-eval-framework[docs]'") from e
            out.append((f.stem, "\n\n".join((pg.extract_text() or "") for pg in PdfReader(str(f)).pages)))
        elif ext == ".docx":
            try:
                import docx
            except ImportError as e:
                raise RuntimeError("reading Word files needs python-docx: pip install 'genai-eval-framework[docs]'") from e
            d = docx.Document(str(f))
            out.append((f.stem, "\n\n".join((("# " if p.style.name.lower().startswith("heading") else "") + p.text)
                                            for p in d.paragraphs if p.text.strip())))
        elif ext in (".jsonl",):                       # an existing corpus: {id, title, text} per line
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    out.append((f"{r.get('id')}|{r.get('title', '')}", r.get("text", "")))
    return [(n, t) for n, t in out if t and t.strip()]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "doc"


def _chunk_doc(name: str, text: str, words: int) -> list[dict[str, Any]]:
    base = _slug(name)
    heading = name.replace("_", " ").replace("-", " ")
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
    return [{"id": f"{base}-{i:02d}", "title": h, "text": " ".join(" ".join(b).split()), "source": name}
            for i, (h, b) in enumerate(groups, 1)]


def chunk(docs: list[tuple[str, str]], words: int = 180) -> list[dict[str, Any]]:
    """Passages of about `words` words, split on paragraphs, each titled with its nearest heading."""
    out: list[dict[str, Any]] = []
    for name, text in docs:
        if "|" in name and name.split("|", 1)[0]:     # pre-chunked corpus rows keep their ids
            pid, title = name.split("|", 1)
            out.append({"id": pid, "title": title or pid, "text": " ".join(text.split()), "source": pid})
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


def seed(passages: list[dict[str, Any]], providers: Any, generator: str, n: int = 40, mix: tuple[float, float, float] = (0.6, 0.2, 0.2),
         tokenizer: Any = None, seed_: int = 7) -> list[dict[str, Any]]:
    """Draft cases from passages: about mix[0] answerable, mix[1] edge, mix[2] unanswerable."""
    import random

    from .providers import ProviderPool
    if not passages:
        raise ValueError("no passages: nothing readable in the documents")
    prov = ProviderPool(providers).get(generator)
    rng = random.Random(seed_)
    n_ans, n_edge = round(n * mix[0]), round(n * mix[1])
    n_un = max(0, n - n_ans - n_edge)
    order = passages[:]
    rng.shuffle(order)
    batches = [order[i:i + 4] for i in range(0, len(order), 4)] or [order]
    jobs = []                                         # (kind, batch, count)
    for kind, total in (("answerable", n_ans), ("edge", n_edge), ("unanswerable", n_un)):
        i = 0
        while total > 0:
            b = batches[i % len(batches)]
            k = min(total, 3)
            jobs.append((kind, b, k))
            total -= k
            i += 1

    async def run_all():
        return await asyncio.gather(*[_ask(prov, SEED_PROMPT.format(
            kind=kind, n=k, passages=_passages_block(b),
            instruction=INSTRUCTIONS[kind].format(kinds=", ".join(EDGE_KINDS)))) for kind, b, k in jobs])
    tok = (lambda x: tokenizer.tokenize(x)) if tokenizer else (lambda x: x)
    by_id = {p["id"]: p for p in passages}
    out, seen = [], set()
    for (kind, b, _), rows in zip(jobs, asyncio.run(run_all())):
        for r in rows:
            q = str(r.get("question") or "").strip()
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
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
    return out[:n] if len(out) > n else out
