"""Offline stand-ins for the systems under test, so the demo runs with no API keys and no network.

In live mode the same suites point at real targets (prompt + model, RAG over a knowledge base, a tool-calling agent
on OpenAI / Bedrock / AgentCore / Vertex) by switching to providers.live.yaml and the *_live targets.
Costs and latencies returned here are simulated and labelled as such in the demo output.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).parent
POLICY_DOCS = json.loads((HERE / "data" / "policy_wording.json").read_text(encoding="utf-8"))
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


# ---------------------------------------------------------------------------
# 1. Claim-form extraction: two versions of an extraction system
# ---------------------------------------------------------------------------
def _find(pattern: str, text: str, flags=re.I) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _date_v1(text: str) -> str | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text) or re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return None
    a, b, c = m.groups()
    return f"{a}-{b}-{c}" if len(a) == 4 else f"{c}-{b}-{a}"


def _date_v2(text: str) -> str | None:
    d = _date_v1(text)
    if d:
        return d
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", text)
    if m:
        return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def _amount_v1(text: str) -> float | None:
    m = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _amount_v2(text: str) -> float | None:
    m = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|lakhs|k)?", text, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * {"lakh": 1e5, "lac": 1e5, "lakhs": 1e5, "k": 1e3}.get((m.group(2) or "").lower(), 1)


def _incident(text: str) -> str | None:
    t = text.lower()
    for k, words in (("theft", ["stolen", "theft", "chori"]), ("flood", ["flood", "water logging", "waterlogged", "submerged"]),
                     ("fire", ["fire", "burnt", "caught fire"]), ("collision", ["collision", "hit", "accident", "crash", "rear-ended", "takkar"])):
        if any(w in t for w in words):
            return k
    return None


def _extract(text: str, v2: bool) -> dict:
    return {
        "policy_number": _find(r"\b(MOT-\d{4}-\d{6})\b", text),
        "claimant_name": _find(r"(?i:name|naam|claimant)[ \t]*[:\-][ \t]*([A-Z][a-z]+(?:[ ][A-Z][a-z]+){0,2})", text, 0) or
        _find(r"(?i:this is)[ ]+([A-Z][a-z]+(?:[ ][A-Z][a-z]+)?)", text, 0) or
        (_find(r"(?i:i am|main)[ ]+([A-Z][a-z]+(?:[ ][A-Z][a-z]+)?)", text, 0) if v2 else None),
        "incident_date": _date_v2(text) if v2 else _date_v1(text),
        "claim_amount": _amount_v2(text) if v2 else _amount_v1(text),
        "vehicle_reg": (re.sub(r"\s+", " ", _find(r"\b([A-Z]{2}\s?\d{1,2}[A-Z]?\s?[A-Z]{1,3}\s?\d{4})\b", text, 0) or "") or None),
        "incident_type": _incident(text),
    }


def extractor_v1(case, ctx):
    out = _extract(case.input_text(), v2=False)
    return {"output": out, "latency_ms": 820 + _rng(case.id, "v1").randint(0, 300), "cost_usd": 0.0009, "model": "simulated-small"}


def extractor_v2(case, ctx):
    out = _extract(case.input_text(), v2=True)
    return {"output": out, "latency_ms": 1150 + _rng(case.id, "v2").randint(0, 450), "cost_usd": 0.0016, "model": "simulated-large"}


# ---------------------------------------------------------------------------
# 2. Policy Q&A (RAG): retriever + two answerers
# ---------------------------------------------------------------------------
STOP = set("the a an and or of to in on for with is are was be it this that as at by from my i me can do does what when how if will "
           "under policy cover covered my car".split())


def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def retrieve(query: str, k: int = 4) -> list[dict]:
    q = _toks(query)
    scored = []
    for d in POLICY_DOCS:
        overlap = len(q & _toks(d["title"] + " " + d["text"]))
        scored.append((overlap / (len(q) or 1), d))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [{"id": d["id"], "text": d["text"], "score": round(s, 3), "source": d["title"]} for s, d in scored[:k]]


def _best_sentences(query: str, chunks: list[dict], n: int) -> list[tuple[str, str]]:
    q = _toks(query)
    cands = []
    for c in chunks:
        for s in re.split(r"(?<=[.])\s+", c["text"]):
            cands.append((len(q & _toks(s)), c["id"], s.strip()))
    cands.sort(key=lambda x: -x[0])
    return [(cid, s) for _, cid, s in cands[:n]]


def rag_v1(case, ctx):
    """k=2, always answers, and appends a confident-sounding line that is not in the policy."""
    q = case.input_text()
    chunks = retrieve(q, 2)
    sents = _best_sentences(q, chunks, 1)
    ans = " ".join(f"{s} [{cid}]" for cid, s in sents)
    if _rng(case.id, "rag1").random() < 0.45:
        ans += " Claims like this are usually settled within 7 days."
    return {"output": ans, "context": chunks, "latency_ms": 1300 + _rng(case.id).randint(0, 500), "cost_usd": 0.0011}


def rag_v2(case, ctx):
    """k=4, declines when retrieval support is weak, cites every sentence it uses."""
    q = case.input_text()
    chunks = retrieve(q, 4)
    if not chunks or chunks[0]["score"] < 0.3:
        return {"output": "I can't find this in your policy documents, so I can't confirm it. Please contact the claims desk for help.",
                "context": chunks, "latency_ms": 1500, "cost_usd": 0.0013}
    sents = _best_sentences(q, chunks, 2)
    ans = " ".join(f"{s} [{cid}]" for cid, s in sents)
    return {"output": ans, "context": chunks, "latency_ms": 1650 + _rng(case.id).randint(0, 700), "cost_usd": 0.0019}


# ---------------------------------------------------------------------------
# 3. FNOL claims agent: an offline "policy interpreter" that follows the versioned system prompt
# ---------------------------------------------------------------------------
def fnol_agent(case, ctx):
    """Simulated FNOL agent. `options: {model: fast}` simulates a cheaper, faster model: about a third of the cost and
    half the latency, but it skips the policy check on roughly a quarter of conversations (varies by repeat)."""
    fast = (ctx.get("options") or {}).get("model") == "fast"
    skip = fast and random.Random(f"{case.id}:{ctx['repeat']}:fast").random() < 0.25
    out = _fnol_agent(case, {**ctx, "skip_verify": skip})
    if fast:
        out["latency_ms"] = round(out["latency_ms"] * 0.5)
        out["cost_usd"] = round(out["cost_usd"] * 0.35, 6)
    return out


def _fnol_agent(case, ctx):
    """Stand-in for an LLM agent. It reads the *versioned* system prompt (prompts/claims_agent/<version>) and follows the
    behaviours the prompt asks for, so changing the prompt version changes behaviour exactly as a prompt edit would.
    Live mode: use target `fnol-agent-live` (tool_loop over a real model with the same prompt and insurance_tools)."""
    import insurance_tools as tools
    run = ctx["run"]
    if ctx.get("prompt"):                     # the target's prompt (pinned version, stage or --prompt override)
        rp = ctx["prompt"]({"language": "en"})
    else:
        ver = run.prompt_overrides.get("claims_agent")
        rp = run.prompts.render(f"claims_agent@{ver}" if ver else "claims_agent", "prod", {"language": "en"})
    sysp = (rp.system or "").lower()
    verify_first = "verify the policy" in sysp and not ctx.get("skip_verify")
    escalate_injury = "injur" in sysp and "escalate" in sysp
    no_promises = "never promise" in sysp
    state = tools.make_state(case)
    traj, rng = [], _rng(case.id, ctx["repeat"], rp.version)
    text = " ".join(case.turns or [case.input_text()])
    pol = _find(r"\b(MOT-\d{4}-\d{6})\b", text)
    inc = _incident(text) or "collision"
    loc = _find(r"(?:near|at|in)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", text, 0) or "unknown"
    date = _date_v2(text) or "2026-11-01"

    def call(name, args):
        res = getattr(tools, name)(args, state)
        traj.append({"name": name, "args": args, "result": res})
        return res

    steps = 1
    if not pol:
        reply = "Could you share your policy number (it looks like MOT-2026-000000)?"
        return {"output": reply, "trajectory": traj, "final_state": state, "steps": steps, "latency_ms": 900, "cost_usd": 0.002,
                "prompt_versions": {rp.id: rp.version}}
    if escalate_injury and re.search(r"injur|hurt|hospital|bleeding|chot", text, re.I):
        call("escalate_to_human", {"reason": "possible injury reported"})
        steps += 1
        reply = "I'm sorry to hear someone was hurt. I've asked a claims specialist to call you right away; please call 112 if you need emergency help."
        return {"output": reply, "trajectory": traj, "final_state": state, "steps": steps, "latency_ms": 1400 + rng.randint(0, 400),
                "cost_usd": 0.004, "prompt_versions": {rp.id: rp.version}}
    if verify_first:
        p = call("lookup_policy", {"policy_number": pol})
        steps += 1
        if p.get("status") != "active":
            reply = (f"Your policy {pol} is not active ({p.get('status', 'not found')}), so I can't register a claim on it. "
                     "I can connect you with our renewals team.")
            return {"output": reply, "trajectory": traj, "final_state": state, "steps": steps, "latency_ms": 1600 + rng.randint(0, 400),
                    "cost_usd": 0.004, "prompt_versions": {rp.id: rp.version}}
    cov = call("check_coverage", {"policy_number": pol, "incident_type": inc})
    steps += 1
    if rng.random() < 0.08:                      # occasional redundant call, as real agents do
        call("check_coverage", {"policy_number": pol, "incident_type": inc})
        steps += 1
    if not cov.get("covered", True):
        reply = f"Your policy doesn't cover {inc} damage ({cov.get('reason', 'excluded')}). I can explain the options available to you."
    else:
        c = call("create_claim", {"policy_number": pol, "incident_type": inc, "incident_date": date, "location": loc})
        steps += 1
        reply = f"I've registered claim {c['claim_id']} for the {inc} on {date}. A surveyor will contact you within 24 hours."
        if not no_promises:
            reply += " Your claim will be paid in full."
    return {"output": reply, "trajectory": traj, "final_state": state, "steps": steps, "latency_ms": 2100 + rng.randint(0, 900),
            "cost_usd": 0.006, "prompt_versions": {rp.id: rp.version}}
