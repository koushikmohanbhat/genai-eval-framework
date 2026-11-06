"""Source connector base: every connector can read recorded fixtures (offline demo, CI) or call the live cloud API."""
from __future__ import annotations

import gzip
import json
import re
import time
from pathlib import Path
from typing import Any, ClassVar

from ..config import SourceSpec
from ..models import Sample, Score

REGISTRY: dict[str, type[Source]] = {}


def register(name: str):
    def deco(cls):
        REGISTRY[name] = cls
        cls.type_name = name
        return cls
    return deco


def parse_window(window: dict[str, Any]) -> tuple[float, float]:
    """{'last': '24h'} or {'start': iso, 'end': iso} -> (start_epoch_s, end_epoch_s)."""
    from datetime import datetime
    end = time.time()
    if window.get("end"):
        end = datetime.fromisoformat(str(window["end"]).replace("Z", "+00:00")).timestamp()
    if window.get("start"):
        return datetime.fromisoformat(str(window["start"]).replace("Z", "+00:00")).timestamp(), end
    m = re.fullmatch(r"(\d+)\s*([smhd])", str(window.get("last", "24h")))
    if not m:
        raise ValueError(f"bad window {window}")
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return end - secs, end


def read_fixture(path: Path) -> list[dict[str, Any]]:
    raw = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text(encoding="utf-8")
    raw = raw.strip()
    if not raw:
        return []
    if raw[0] == "[":
        return json.loads(raw)
    if raw[0] == "{" and "\n" not in raw:
        d = json.loads(raw)
        return d if isinstance(d, list) else [d]
    try:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    except json.JSONDecodeError:
        d = json.loads(raw)
        return d if isinstance(d, list) else [d]


class Source:
    """kind: spans (produce samples), records (request/response logs to join), history (conversation per session),
    metrics (operational baselines), scores (managed evaluator results)."""
    type_name = "base"
    kind: ClassVar[str] = "spans"

    def __init__(self, spec: SourceSpec, base_dir: str = ".", client: Any = None):
        self.spec = spec
        self.base_dir = base_dir
        self._client = client
        self.extra = spec.model_extra or {}

    def opt(self, key: str, default=None):
        return self.extra.get(key, default)

    def fixture_records(self) -> list[dict[str, Any]] | None:
        if not self.spec.fixture:
            return None
        p = Path(self.spec.fixture)
        p = p if p.is_absolute() else Path(self.base_dir) / p
        return read_fixture(p)

    def records(self) -> list[dict[str, Any]]:
        fx = self.fixture_records()
        return fx if fx is not None else self.fetch()

    def fetch(self) -> list[dict[str, Any]]:  # pragma: no cover - live API
        raise NotImplementedError

    # kind-specific outputs
    def samples(self) -> list[Sample]:
        return []

    def join_records(self) -> list[dict[str, Any]]:
        return []

    def history(self, session_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {}

    def metrics(self) -> dict[str, Any]:
        return {}

    def scores(self) -> list[Score]:
        return []

    def trace_url(self, trace_id: str | None) -> str | None:
        return None


def build_source(spec: SourceSpec | dict[str, Any], base_dir: str = ".", client: Any = None) -> Source:
    if isinstance(spec, dict):
        spec = SourceSpec(**{"name": spec.get("name", spec["type"]), **spec})
    if spec.type not in REGISTRY:
        raise ValueError(f"unknown source type '{spec.type}' (available: {sorted(REGISTRY)})")
    return REGISTRY[spec.type](spec, base_dir, client)


def attr_filter(sp: dict[str, Any], flt: dict[str, Any]) -> bool:
    """Client-side span filter on service name and arbitrary attribute equality."""
    res = sp.get("resource", {})
    a = sp.get("attrs", {})
    if flt.get("service_name") and res.get("service.name", a.get("service.name")) != flt["service_name"]:
        return False
    for k, v in (flt.get("attributes") or {}).items():
        if str(a.get(k, res.get(k))) != str(v):
            return False
    return True


def contains_value(sp: dict[str, Any], value: str) -> bool:
    return any(value in str(v) for v in list(sp.get("attrs", {}).values()) + list(sp.get("resource", {}).values()))
