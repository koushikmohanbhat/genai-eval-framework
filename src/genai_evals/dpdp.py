"""DPDP-safe eval data.

Two tokenisers:
  builtin     - offline detectors for structured identifiers (genai_evals.pii: the suite's detector sets, by default
                email, Indian identifiers and payment cards) replaced by keyed, deterministic tokens like
                <AADHAAR_3f9a1c0b7d2e>.
  dpdp_rights - the DPDP Data Rights Framework (catalog 5.5): per-person vault tokens, consent checks and lineage
                registration, so an erasure request also deletes eval rows built from that person's data.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Any, Protocol

from .pii import Finding, detect, luhn_valid, verhoeff_check_digit, verhoeff_valid  # noqa: F401  (re-exported)
from .pii import active_names as _active_names

TOKEN_RE = re.compile(r"<([A-Z_][A-Z0-9_]*)_[0-9a-f]{12}>")


_WARNED: set[str] = set()


def _warn_once(msg: str) -> None:
    import sys
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"warning: {msg}", file=sys.stderr)


class Tokenizer(Protocol):
    def tokenize(self, text: str, principal: str | None = None) -> str: ...


class BuiltinTokenizer:
    """Keyed deterministic tokens: the same value always maps to the same token within a key, so graders can still
    compare tokenised outputs with tokenised references. Set GENAI_EVALS_TOKEN_KEY to keep tokens stable across runs."""

    def __init__(self, key: bytes | str | None = None):
        key = key or os.environ.get("GENAI_EVALS_TOKEN_KEY")
        self.stable = bool(key)
        if not key:
            key = secrets.token_hex(32)
            _warn_once("GENAI_EVALS_TOKEN_KEY is not set: tokens use a random key for this run only, so tokenised "
                       "results and baselines from different runs will not match. Set a stable key from a secret manager.")
        self.key = key.encode() if isinstance(key, str) else key

    def describe(self) -> dict[str, Any]:
        return {"type": "builtin", "stable_key": self.stable, "types": _active_names(), "reversible": False}

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

    def describe(self) -> dict[str, Any]:
        return {"type": "dpdp_rights", "stable_key": True, "reversible": "via the DPDP framework vault only"}

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
