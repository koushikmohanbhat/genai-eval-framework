"""Offline provider for demos, CI and tests. No network, deterministic.

options.mode:
  echo      - returns the last user message
  scripted  - options.responses: {substring: reply}; options.default
  judge     - heuristic judge that understands this framework's judge prompts (<task>...</task>);
              options.strictness (default 1.0) scales scores so two mock judges can disagree
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from ..models import Message, Usage
from .base import Provider, ProviderResponse

STOP = set("the a an and or of to in on for with is are was were be been it this that as at by from your you our we i "
           "has have had not no yes can will would should could may might do does did if then than so but about into over".split())
REFUSAL = re.compile(r"\b(i (?:can(?:no|')t|am unable|am not able)|i don't have (?:that|this|enough) information|not (?:able|allowed) to|"
                     r"cannot (?:help|share|provide)|outside (?:my|the) scope|not covered in the (?:documents|policy))", re.I)


META = re.compile(r"^\s*(please (?:contact|call|reach)|i(?:'ve| have| will|'ll) |you can (?:contact|call)|let me )", re.I)


def words(text: str | None) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOP]


def overlap(a: str | None, b: str | None) -> float:
    """Share of b's content words found in a (recall of b in a)."""
    wb = set(words(b))
    if not wb:
        return 0.0
    return len(wb & set(words(a))) / len(wb)


def tag(text: str, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return m.group(1).strip() if m else None


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if len(words(s)) >= 2]


class MockProvider(Provider):
    retry_base_s = 0.0

    def _complete(self, messages: list[Message], tools, temperature, max_tokens, json_schema, json_mode):
        mode = self.profile.options.get("mode", "echo")
        prompt = "\n".join(m.content or "" for m in messages)
        last_user = next((m.content or "" for m in reversed(messages) if m.role == "user"), "")
        if mode == "judge":
            text = json.dumps(self.judge(prompt))
        elif mode == "scripted":
            text = self.profile.options.get("default", "")
            for k, v in (self.profile.options.get("responses") or {}).items():
                if k.lower() in prompt.lower():
                    text = v
                    break
        else:
            text = last_user
        usage = Usage(input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(text) // 4))
        return ProviderResponse(text=text, usage=usage, latency_ms=5.0, model=self.profile.model)

    # ---- heuristic judge ---------------------------------------------------
    def judge(self, prompt: str) -> dict:
        task = tag(prompt, "task") or "rubric"
        k = float(self.profile.options.get("strictness", 1.0))
        out = tag(prompt, "output") or ""
        ref = tag(prompt, "reference")
        ctx = tag(prompt, "context") or ""
        if task == "faithfulness":
            claims = []
            for s in sentences(out):
                if REFUSAL.search(s) or META.search(s):
                    continue                     # refusals / offers of help are not factual claims
                support = overlap(ctx, s)
                verdict = "supported" if support >= 0.6 * k else "unsupported"
                claims.append({"claim": s, "verdict": verdict, "evidence": f"{support:.0%} of claim terms found in context"})
            return {"claims": claims}
        if task == "answer_relevance":
            q = tag(prompt, "question") or ""
            s = min(1.0, overlap(out, q) * 1.5 / k)
            return {"score": round(1 + 4 * s), "reasoning": f"answer covers {overlap(out, q):.0%} of the question's terms"}
        if task == "pairwise":
            a, b = tag(prompt, "output_a") or "", tag(prompt, "output_b") or ""
            basis = ref or tag(prompt, "question") or ""
            sa, sb = overlap(a, basis), overlap(b, basis)
            winner = "tie" if abs(sa - sb) < 0.05 else ("A" if sa > sb else "B")
            return {"winner": winner, "reasoning": f"A covers {sa:.0%}, B covers {sb:.0%} of the reference"}
        if task == "policy":
            pol = tag(prompt, "policy") or ""
            banned = [p.lower() for p in re.findall(r'"([^"]+)"', pol)]
            hits = [p for p in banned if p in out.lower()]
            return {"pass": not hits, "score": 0 if hits else 1, "reasoning": f"found {hits}" if hits else "no violations found"}
        if task == "refusal":
            refused = bool(REFUSAL.search(out))
            return {"refused": refused, "reasoning": "refusal language present" if refused else "answered"}
        if task == "simulate_user":
            turn = int(tag(prompt, "turn") or 1)
            goal = tag(prompt, "goal") or ""
            return {"message": "[DONE]" if turn > 1 else goal}
        # rubric / reference_match
        scale = tag(prompt, "scale") or "1-5"
        if ref:
            s = overlap(out, ref)
        else:
            s = 0.85 if words(out) else 0.0
        s = max(0.0, min(1.0, s ** k))
        if scale == "pass_fail" or task == "reference_match":
            ok = s >= 0.6
            return {"pass": ok, "score": 1 if ok else 0, "reasoning": f"{s:.0%} of reference terms present"}
        return {"score": max(1, min(5, round(1 + 4 * s))), "reasoning": f"{s:.0%} of reference terms present"}

    def _embed(self, texts):
        dim = 64
        out = []
        for t in texts:
            v = [0.0] * dim
            for w in words(t):
                v[int(hashlib.md5(w.encode(), usedforsecurity=False).hexdigest(), 16) % dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out
