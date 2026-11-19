"""Anonymised usage summary a partner can choose to share (`genai-evals usage export`).

Off by default and never sent anywhere: genai-evals makes no network call for this. The command writes a file the
partner reads (`usage schema` shows every field) and decides to send to whoever runs their partner programme.

What it holds: framework and pack versions, overlays, run profile and mode, case counts in ranges, gate outcome
counts, adapter kinds and validation status, provider types, and the month of each run.
What it never holds: case ids, inputs, outputs, references, scores per case, prompts, model ids, account or project
ids, URLs, file paths, run ids, suite names, or names a partner or customer wrote (gate, pack, overlay and profile
names that aren't built in are replaced by a salted hash, so they can be counted but not read).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = {
    "schema": "genai-evals/usage", "schema_version": "1.0",
    "fields": {
        "framework_version": "genai-evals version that wrote the summary",
        "generated_month": "YYYY-MM the file was written",
        "project": "salted hash of the suite name: tells runs of one project apart, can't be reversed to it",
        "runs[].month": "YYYY-MM of the run",
        "runs[].mode": "offline | online | import:promptfoo | engine:promptfoo",
        "runs[].pack": "built-in pack@version, or a salted hash for a partner's own pack",
        "runs[].overlays": "built-in overlay names; others hashed",
        "runs[].profile": "quickstart | pr-check | release-gate, or a hash for a custom profile; null without one",
        "runs[].cases": "range: 1-29, 30-99, 100-299, 300-999, 1000+",
        "runs[].targets": "number of targets", "runs[].repeats": "repeats per case",
        "runs[].judges": "number of judges",
        "runs[].calibrated_judges": "judges with calibration evidence attached",
        "runs[].gates": "counts of pass / fail / invalid gates, and skipped by a profile",
        "runs[].gate_outcomes": "per gate: outcome; built-in gate names as is, others hashed",
        "runs[].adapters": "kind, built-in name (others hashed), validation status",
        "runs[].provider_types": "openai, anthropic, bedrock, vertex, ... (no model ids)",
        "runs[].passed": "whether the run's blocking gates passed",
    },
    "never": ["case ids", "inputs, outputs, references", "per-case scores", "prompts", "model ids", "account / project ids",
              "URLs and file paths", "run ids and git SHAs", "suite names", "names partners or customers wrote"],
}
BUILTIN_PROFILES = {"quickstart", "pr-check", "release-gate"}


def _builtin_names() -> dict[str, set[str]]:
    """Pack, overlay and gate names that ship with genai-evals: safe to report as is."""
    import yaml

    from . import packs
    out: dict[str, set[str]] = {"packs": set(), "overlays": set(), "gates": set(), "adapters": set()}
    for name in packs.builtin():
        d = packs.resolve(name)
        out["packs"].add(name)
        suite = yaml.safe_load((d / "suite.yaml").read_text(encoding="utf-8")) or {}
        out["gates"] |= {g.get("name") for g in suite.get("gates", []) if isinstance(g, dict)}
        for prof in (suite.get("profiles") or {}).values():
            out["gates"] |= {g.get("name") for g in (prof or {}).get("add_gates", []) if isinstance(g, dict)}
        for o in (packs.manifest(d).get("overlays") or []):
            out["overlays"].add(o["name"])
            ov = yaml.safe_load((d / "overlays" / o["name"] / "overlay.yaml").read_text(encoding="utf-8")) or {}
            out["gates"] |= {g.get("name") for g in ov.get("gates", []) if isinstance(g, dict)}
    return out


def _h(salt: str, kind: str, value: str) -> str:
    return f"{kind}:" + hashlib.sha256(f"{salt}:{kind}:{value}".encode()).hexdigest()[:10]


def _bucket(n: int) -> str:
    for hi, label in ((29, "1-29"), (99, "30-99"), (299, "100-299"), (999, "300-999")):
        if n <= hi:
            return label
    return "1000+"


def summarise(result: dict[str, Any], salt: str, known: dict[str, set[str]]) -> dict[str, Any]:
    def name(kind: str, v: str | None, allowed: set[str]) -> str | None:
        if v is None:
            return None
        return v if v in allowed else _h(salt, kind, v)

    pack = result.get("pack")
    pack_name, _, ver = str(pack or "").partition("@")
    prof = (result.get("profile") or {}).get("name")
    gates = result.get("gates") or []
    status = lambda g: g.get("status") or ("pass" if g.get("passed") else "fail")  # noqa: E731
    adapters = []
    for a in result.get("adapters") or []:
        builtin = a.get("origin", "builtin") == "builtin"
        adapters.append({"kind": a.get("kind"), "name": a.get("name") if builtin else _h(salt, "adapter", str(a.get("name"))),
                         "status": a.get("status")})
    provider_types = sorted({a["name"] for a in adapters if a["kind"] == "provider" and ":" not in str(a["name"])})
    calib = result.get("calibration") or {}
    return {
        "month": str(result.get("created_at") or "")[:7] or None,
        "mode": result.get("mode", "offline"),
        "pack": (pack_name + (f"@{ver}" if ver else "")) if pack_name in known["packs"] else
                (_h(salt, "pack", pack_name) if pack_name else None),
        "overlays": [name("overlay", o, known["overlays"]) for o in
                     sorted({Path(str(o)).name for o in (result.get("overlays") or [])})],
        "profile": name("profile", prof, BUILTIN_PROFILES),
        "cases": _bucket(len(result.get("cases") or [])),
        "targets": len(result.get("targets") or []),
        "repeats": result.get("repeats"),
        "judges": len(result.get("judges") or []),
        "calibrated_judges": len(calib) if isinstance(calib, dict) else 0,
        "gates": {"pass": sum(status(g) == "pass" for g in gates), "fail": sum(status(g) == "fail" for g in gates),
                  "invalid": sum(status(g) == "invalid" for g in gates),
                  "skipped": len((result.get("profile") or {}).get("skipped_gates") or [])},
        "gate_outcomes": sorted({(name("gate", g.get("gate"), known["gates"]), status(g)) for g in gates}),
        "adapters": adapters,
        "provider_types": provider_types,
        "passed": bool(result.get("passed")),
    }


def find_results(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths or [".genai-evals", "out"]:
        q = Path(p)
        if q.is_file() and q.name.endswith(".json"):
            out.append(q)
        elif q.is_dir():
            out += sorted(q.rglob("result.json"))
    return out


def export(paths: list[str], salt: str | None = None) -> tuple[dict[str, Any], list[str]]:
    notes = []
    if not salt:
        salt = secrets.token_hex(16)
        notes.append("no GENAI_EVALS_USAGE_SALT: hashes use a random salt, so they won't match across exports")
    known = _builtin_names()
    runs, projects = [], set()
    for f in find_results(paths):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("schema") != "genai-evals/result":
            continue
        runs.append(summarise(r, salt, known))
        projects.add(_h(salt, "project", str(r.get("suite"))))
    for run in runs:                                 # tuples -> lists for JSON
        run["gate_outcomes"] = [list(x) for x in run["gate_outcomes"]]
    from . import __version__
    doc = {"schema": SCHEMA["schema"], "schema_version": SCHEMA["schema_version"], "framework_version": __version__,
           "generated_month": datetime.now(timezone.utc).strftime("%Y-%m"),
           "project": sorted(projects)[0] if len(projects) == 1 else sorted(projects), "runs": runs}
    return doc, notes


def salt_from_env() -> str | None:
    return os.environ.get("GENAI_EVALS_USAGE_SALT")
