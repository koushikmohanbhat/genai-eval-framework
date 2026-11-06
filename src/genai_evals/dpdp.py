"""DPDP-safe eval data.

Two tokenisers:
  builtin     - offline regex detectors for Indian identifiers (Aadhaar with Verhoeff check, PAN, UPI, +91 phones,
                email, card numbers with Luhn) replaced by keyed, deterministic tokens like <AADHAAR_3f9a1c0b7d2e>.
  dpdp_rights - the DPDP Data Rights Framework (catalog 5.5): per-person vault tokens, consent checks and lineage
                registration, so an erasure request also deletes eval rows built from that person's data.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def luhn_valid(number: str) -> bool:
    total, alt = 0, False
    for ch in reversed(number):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+\b")),
    ("UPI_ID", re.compile(r"\b[A-Za-z0-9.\-_]{2,256}@(?:ok)?[a-zA-Z]{2,64}\b(?!\.)")),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)")),
    ("CARD_NUMBER", re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b")),
    ("PHONE_IN", re.compile(r"(?<![\d+])(?:\+91[\-\s]?|0)?[6-9]\d{4}[\-\s]?\d{5}(?!\d)")),
]
TOKEN_RE = re.compile(r"<([A-Z_]+)_[0-9a-f]{12}>")


@dataclass
class Finding:
    type: str
    start: int
    end: int
    text: str


def detect(text: str, types: set[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for etype, pat in PATTERNS:
        if types and etype not in types:
            continue
        for m in pat.finditer(text or ""):
            digits = re.sub(r"\D", "", m.group(0))
            if etype == "AADHAAR" and not verhoeff_valid(digits):
                continue
            if etype == "CARD_NUMBER" and not (13 <= len(digits) <= 19 and luhn_valid(digits)):
                continue
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append(Finding(etype, m.start(), m.end(), m.group(0)))
    return sorted(out, key=lambda f: f.start)


class Tokenizer(Protocol):
    def tokenize(self, text: str, principal: str | None = None) -> str: ...


class BuiltinTokenizer:
    """Keyed deterministic tokens: the same value always maps to the same token within a key, so graders can still
    compare tokenised outputs with tokenised references. Set GENAI_EVALS_TOKEN_KEY to keep tokens stable across runs."""

    def __init__(self, key: bytes | str | None = None):
        key = key or os.environ.get("GENAI_EVALS_TOKEN_KEY") or secrets.token_hex(32)
        self.key = key.encode() if isinstance(key, str) else key

    def token(self, etype: str, value: str) -> str:
        norm = re.sub(r"[\s\-]", "", value).lower()
        return f"<{etype}_{hmac.new(self.key, f'{etype}:{norm}'.encode(), hashlib.sha256).hexdigest()[:12]}>"

    def tokenize(self, text: str, principal: str | None = None) -> str:
        if not text:
            return text
        parts, last = [], 0
        for f in detect(text):
            parts.append(text[last:f.start])
            parts.append(self.token(f.type, f.text))
            last = f.end
        parts.append(text[last:])
        return "".join(parts)


class DPDPRightsTokenizer:
    """Delegates to the DPDP Data Rights Framework (`pip install genai-eval-framework[dpdp]`)."""

    def __init__(self, config_path: str, purpose: str = "evaluation", default_principal: str = "eval-dataset"):
        from dpdp_rights.framework import DataRightsFramework  # type: ignore
        self.fw = DataRightsFramework.from_yaml(config_path)
        self.purpose = purpose
        self.default_principal = default_principal

    def tokenize(self, text: str, principal: str | None = None) -> str:
        if not text:
            return text
        pid = self.fw.principal(principal or self.default_principal)
        with self.fw.context(pid, self.purpose):
            return self.fw.protect(text, enforce_consent=False).text

    def register_eval_row(self, content: str, locator: dict[str, Any], principal: str | None = None) -> str:
        """Register a dataset row as an `eval_row` artifact so an erasure request deletes it. Returns the artifact id."""
        pid = self.fw.principal(principal or self.default_principal)
        with self.fw.context(pid, self.purpose):
            art = self.fw.track_artifact("eval_row", "genai_evals_dataset", locator, content=content)
        return getattr(art, "artifact_id", None) or getattr(art, "id", "")


def build_tokenizer(cfg: dict[str, Any] | None) -> Tokenizer:
    cfg = cfg or {}
    if cfg.get("type") == "dpdp_rights":
        return DPDPRightsTokenizer(cfg["config"], cfg.get("purpose", "evaluation"))
    return BuiltinTokenizer(cfg.get("key"))


def tokenize_obj(obj: Any, tok: Tokenizer, principal: str | None = None) -> Any:
    if isinstance(obj, str):
        return tok.tokenize(obj, principal)
    if isinstance(obj, list):
        return [tokenize_obj(x, tok, principal) for x in obj]
    if isinstance(obj, dict):
        return {k: tokenize_obj(v, tok, principal) for k, v in obj.items()}
    return obj
