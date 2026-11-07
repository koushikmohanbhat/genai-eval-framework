"""The versioned result format: identify, upgrade and validate result JSON from any version of the framework.

Rules: a result carries `schema: genai-evals/result` and `schema_version: "<major>.<minor>"`. Minor versions only add
optional fields, so any 1.x reader reads any 1.x result. Results written before versioning (framework <= 0.2) are
upgraded in memory to 1.0 when loaded. A newer major version is refused rather than misread.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA_ID = "genai-evals/result"
SCHEMA_VERSION = "1.0"
SUPPORTED_MAJOR = 1


class ResultSchemaError(ValueError):
    pass


def stamp(result: dict[str, Any]) -> dict[str, Any]:
    from . import __version__
    result.setdefault("schema", SCHEMA_ID)
    result.setdefault("schema_version", SCHEMA_VERSION)
    result.setdefault("framework_version", __version__)
    return result


def upgrade(result: dict[str, Any]) -> dict[str, Any]:
    """Bring a result to the current 1.x shape (in memory)."""
    if result.get("schema") not in (None, SCHEMA_ID):
        raise ResultSchemaError(f"not a genai-evals result (schema={result.get('schema')!r})")
    ver = str(result.get("schema_version") or "0")
    major = int(ver.split(".")[0])
    if major > SUPPORTED_MAJOR:
        raise ResultSchemaError(f"result schema {ver} is newer than this framework reads (1.x); upgrade genai-eval-framework")
    if major == 0:                      # pre-versioning results (<= 0.2.0)
        result = dict(result)
        result["schema"], result["schema_version"] = SCHEMA_ID, SCHEMA_VERSION
        result.setdefault("framework_version", "<=0.2.0")
        result.setdefault("mode", "offline")
        result.setdefault("graders", [])
        result.setdefault("adapters", [])
        for g in result.get("gates", []):
            g.setdefault("status", "pass" if g.get("passed") else "fail")
        result["upgraded_from"] = ver
    return result


def json_schema() -> dict[str, Any]:
    return json.loads((resources.files("genai_evals") / "schemas" / "result-1.0.schema.json").read_text(encoding="utf-8"))


def validate(result: dict[str, Any]) -> list[str]:
    """Problems with a result against the 1.x JSON Schema (empty list = valid)."""
    import jsonschema
    v = jsonschema.Draft202012Validator(json_schema())
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in sorted(v.iter_errors(result), key=lambda e: list(e.absolute_path))]


def load(path: str | Path) -> dict[str, Any]:
    return upgrade(json.loads(Path(path).read_text(encoding="utf-8")))
