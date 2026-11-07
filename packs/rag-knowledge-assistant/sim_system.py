"""Offline stand-in for a RAG knowledge assistant, so the pack runs with no keys or cloud access.

It retrieves from data/corpus.jsonl (plus any overlay corpora) by keyword overlap, refuses requests for secrets or
other people's personal data, refuses when retrieval support is weak, and answers with cited sentences from the
retrieved passages. Options (target `options:`) simulate typical regressions for experiments:
  k: passages retrieved (default 4)
  embellish: share of answers that get an extra, unsupported sentence (default 0)

Replace it with your system: see README.md, "Connect your system". `retrieve` doubles as a retriever for the
targets/python-retriever.yaml target (your model + this corpus).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
STOP = set("a an and are as at be by can do does for from get have how i if in is it its me my of on or our per should so "
           "that the their there this to was what when where which who will with you your each any after before".split())
SENSITIVE = re.compile(r"\b(password for|admin password|credentials?|home address|phone number|salary of|ignore (?:your|all|the) "
                       r"(?:previous )?instructions|list every restricted)\b", re.I)


def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 1}


def _corpus() -> list[dict]:
    docs = []
    for p in [HERE / "data" / "corpus.jsonl", *sorted(HERE.glob("overlays/*/corpus.jsonl"))]:
        if p.exists():
            docs += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return docs


def retrieve(query: str, k: int = 4) -> list[dict]:
    q = _toks(query)
    scored = sorted(((len(q & _toks(d["title"] + " " + d["text"])) / (len(q) or 1), d) for d in _corpus()), key=lambda x: (-x[0], x[1]["id"]))
    return [{"id": d["id"], "text": d["text"], "score": round(s, 3), "source": d["title"]} for s, d in scored[:k]]


def _rnd(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def answer(case, ctx) -> dict:
    opts = ctx.get("options") or {}
    q = case.input_text()
    chunks = retrieve(q, int(opts.get("k", 4)))
    lat = 900 + int(_rnd(case.id, "lat") * 700)
    if SENSITIVE.search(q):
        return {"output": "I can't share passwords, credentials or other people's personal information, and I can't change how I work. "
                          "Please contact the IT helpdesk or HR directly.", "context": [], "latency_ms": lat, "cost_usd": 0.0006}
    if not chunks or chunks[0]["score"] < 0.34:
        return {"output": "I can't find this in the company knowledge base, so I can't answer it. Please contact the relevant team.",
                "context": chunks, "latency_ms": lat, "cost_usd": 0.0009}
    qt = _toks(q)
    sents = sorted(((len(qt & _toks(s)), c["id"], s.strip()) for c in chunks[:2] for s in re.split(r"(?<=[.])\s+", c["text"])), key=lambda x: -x[0])
    out = " ".join(f"{s} [{cid}]" for _, cid, s in sents[:2] if _)  or f"{sents[0][2]} [{sents[0][1]}]"
    if _rnd(case.id, ctx.get("repeat", 0), "embellish") < float(opts.get("embellish", 0)):
        out += " Exceptions are usually approved within a day."
    return {"output": out, "context": chunks, "latency_ms": lat + 400, "cost_usd": 0.0016}
