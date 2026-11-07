"""Conformance kit: the same checks for every adapter, built-in or plugin.

Sources are checked on a recorded fixture (and, where the spec supplies a fake client, on the live code path):

  formats         the fixture as JSONL, a JSON array and gzip gives identical output
  normalisation   output has the cloud-neutral shape for the adapter's kind (Sample / join record / history / metrics / Score)
  deterministic   same output on a second read and with records shuffled (no dependence on arrival order)
  missing_fields  records with fields removed at random never crash the adapter (malformed records are skipped)
  empty           no records gives empty output, not an error
  timestamps      span start times are normalised to epoch nanoseconds in a sane range
  pagination      the live path follows page tokens and returns the same output as the fixture   (needs client_factory)
  retries         a throttled first call is retried and the fetch still succeeds                 (needs client_factory)

Providers are checked with a fake SDK client: response normalisation (text, tool calls, usage, model), retry on
transient errors, no retry on client errors, JSON mode accepted.

A check the spec cannot exercise is reported as `not_covered`, never as a pass. Partners use the kit for their own
adapters (`genai-evals conformance --spec my_pkg.conformance:SPECS`) or from pytest (`assert_conformant(spec)`).
"""
from __future__ import annotations

import asyncio
import copy
import gzip
import json
import random
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable

PASS, FAIL, NOT_COVERED = "pass", "fail", "not_covered"
SOURCE_CHECKS = ("formats", "normalisation", "deterministic", "missing_fields", "empty", "timestamps", "pagination", "retries")
PROVIDER_CHECKS = ("normalisation", "tool_calls", "retries", "no_retry_on_client_error", "json_mode")
TS_MIN, TS_MAX = 946684800 * 10**9, 4102444800 * 10**9      # 2000-01-01 .. 2100-01-01 in ns


@dataclass
class SourceConformance:
    type: str
    fixture: str | Path                                   # recorded records (JSONL, JSON array or .gz)
    options: dict[str, Any] = field(default_factory=dict)  # extra source options (region, log_group, ...)
    min_items: int = 1                                     # the fixture must produce at least this many items
    expect_fields: list[str] = field(default_factory=list)  # Sample fields that must be set on at least one sample
    session_ids: list[str] | None = None                   # history sources: sessions to ask for (default: from records)
    client_factory: Callable[[list[dict[str, Any]], int, int], Any] | None = None   # (records, page_size, failures) -> fake client
    source_class: Any = None                               # plugin class (default: looked up by type)


@dataclass
class ProviderConformance:
    provider: str
    profile: dict[str, Any]
    client_factory: Callable[[], Any]                      # fake SDK client returning one canned response with text + a tool call
    expect_text: str
    expect_tool: str | None = None


@dataclass
class CheckResult:
    kind: str
    adapter: str
    check: str
    status: str
    detail: str = ""


# ---- helpers ---------------------------------------------------------------------
def _fixture_dir() -> Path:
    return Path(str(resources.files("genai_evals") / "conformance" / "fixtures"))


def _read(path: Path) -> list[dict[str, Any]]:
    from ..sources.base import read_fixture
    return read_fixture(path)


def _build(spec: SourceConformance, fixture: Path | None, client: Any = None):
    from ..config import SourceSpec
    from ..sources.base import REGISTRY, build_source
    s = SourceSpec(name=f"conf-{spec.type}", type=spec.type, fixture=str(fixture) if fixture else None, **spec.options)
    if spec.source_class is not None:
        REGISTRY[spec.type] = spec.source_class
    src = build_source(s, ".", client)
    src.retry_base_s = 0.0
    return src


def _sessions(spec: SourceConformance, records: list[dict[str, Any]]) -> list[str]:
    if spec.session_ids is not None:
        return spec.session_ids
    ids = []
    for r in records:
        sid = r.get("sessionId") or r.get("session_id") or (r.get("name", "").split("/sessions/")[-1].split("/")[0] if "/sessions/" in str(r.get("name", "")) else None)
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def output(src, spec: SourceConformance, records: list[dict[str, Any]]) -> Any:
    """The adapter's cloud-neutral output for its kind, as plain JSON-able data."""
    kind = src.kind
    if kind == "spans":
        return [json.loads(s.model_dump_json()) for s in src.samples()]
    if kind == "records":
        return src.join_records()
    if kind == "history":
        return src.history(_sessions(spec, records))
    if kind == "metrics":
        return src.metrics()
    if kind == "scores":
        return [json.loads(s.model_dump_json()) for s in src.scores()]
    raise ValueError(f"unknown source kind {kind}")


def _canon(out: Any) -> Any:
    if isinstance(out, list):
        return sorted(json.dumps(x, sort_keys=True, default=str) for x in out)
    if isinstance(out, dict):
        return {k: _canon(v) for k, v in sorted(out.items())}
    return out


def _count(out: Any) -> int:
    return len(out) if isinstance(out, (list, dict)) else (1 if out else 0)


def _drop(obj: Any, rng: random.Random, p: float) -> Any:
    if isinstance(obj, dict):
        return {k: _drop(v, rng, p) for k, v in obj.items() if rng.random() >= p}
    if isinstance(obj, list):
        return [_drop(v, rng, p) for v in obj]
    return obj


def _with_fixture(records: list[dict[str, Any]], fn: Callable[[Path], Any], fmt: str = "jsonl") -> Any:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ("f.json" if fmt == "json" else "f.jsonl.gz" if fmt == "gz" else "f.jsonl")
        if fmt == "json":
            p.write_text(json.dumps(records), encoding="utf-8")
        else:
            data = "".join(json.dumps(r) + "\n" for r in records).encode()
            p.write_bytes(gzip.compress(data) if fmt == "gz" else data)
        return fn(p)


def _shape_problems(kind: str, out: Any, spec: SourceConformance) -> list[str]:
    probs = []
    if kind == "spans":
        for s in out:
            if not s.get("case_id") or not s.get("target"):
                probs.append("sample without case_id / target")
            if not str(s.get("origin", "")).startswith("source:"):
                probs.append(f"sample origin {s.get('origin')!r} should be 'source:<name>'")
            if s.get("latency_ms") is not None and s["latency_ms"] < 0:
                probs.append("negative latency")
        for f in spec.expect_fields:
            if out and not any(s.get(f) not in (None, "", [], {}) for s in out):
                probs.append(f"no sample has '{f}'")
    elif kind == "records":
        for r in out:
            miss = {"input_messages", "output_text", "usage"} - set(r)
            if miss:
                probs.append(f"join record missing {sorted(miss)}")
            if r.get("time_ns") is not None and not isinstance(r["time_ns"], int):
                probs.append("time_ns must be an int (epoch ns)")
    elif kind == "history":
        if not isinstance(out, dict):
            probs.append("history must map session id -> messages")
        else:
            for sid, msgs in out.items():
                if any(not isinstance(m, dict) or "role" not in m or "content" not in m for m in msgs):
                    probs.append(f"history for {sid} has messages without role / content")
    elif kind == "metrics":
        if not isinstance(out, dict):
            probs.append("metrics must be a dict")
    elif kind == "scores":
        for s in out:
            v = s.get("value")
            if v is not None and not 0 <= v <= 1:
                probs.append(f"score value {v} outside 0..1")
            if not s.get("grader") or not s.get("case_id"):
                probs.append("score without grader / case_id")
    return sorted(set(probs))[:5]


# ---- source checks ---------------------------------------------------------------
def check_source(spec: SourceConformance) -> list[CheckResult]:
    res: list[CheckResult] = []
    path = Path(spec.fixture)
    path = path if path.is_absolute() or path.exists() else _fixture_dir() / path

    def add(check, ok, detail=""):
        res.append(CheckResult("source", spec.type, check, PASS if ok is True else (NOT_COVERED if ok is None else FAIL), detail))

    try:
        records = _read(path)
        src = _build(spec, path)
        base = output(src, spec, records)
    except Exception as e:  # noqa: BLE001
        add("normalisation", False, f"could not read fixture: {type(e).__name__}: {e}")
        return res
    kind = src.kind

    outs = {}
    for fmt in ("jsonl", "json", "gz"):
        try:
            outs[fmt] = _canon(_with_fixture(records, lambda p: output(_build(spec, p), spec, records), fmt))
        except Exception as e:  # noqa: BLE001
            outs[fmt] = f"error: {e}"
    add("formats", len({json.dumps(v, sort_keys=True, default=str) for v in outs.values()}) == 1, "" if len(set(map(str, outs.values()))) == 1 else "outputs differ by file format")

    probs = _shape_problems(kind, base, spec)
    if _count(base) < spec.min_items:
        probs.insert(0, f"fixture produced {_count(base)} item(s), expected at least {spec.min_items}")
    add("normalisation", not probs, "; ".join(probs) or f"{_count(base)} {kind} item(s)")

    shuffled = records[:]
    random.Random(1).shuffle(shuffled)
    try:
        again = _canon(output(_build(spec, path), spec, records))
        sh = _canon(_with_fixture(shuffled, lambda p: output(_build(spec, p), spec, shuffled)))
        add("deterministic", again == _canon(base) and sh == _canon(base), "" if sh == _canon(base) else "output depends on record order")
    except Exception as e:  # noqa: BLE001
        add("deterministic", False, f"{type(e).__name__}: {e}")

    errs = []
    for seed in range(3):
        mutated = [_drop(copy.deepcopy(r), random.Random(seed * 1000 + i), 0.3) for i, r in enumerate(records)]
        try:
            _with_fixture(mutated, lambda p, m=mutated: output(_build(spec, p), spec, m))
        except Exception as e:  # noqa: BLE001
            errs.append(f"seed {seed}: {type(e).__name__}: {e}")
    add("missing_fields", not errs, "; ".join(errs[:2]))

    try:
        empty = _with_fixture([], lambda p: output(_build(spec, p), spec, []))
        add("empty", _count(empty) == 0, "" if _count(empty) == 0 else "items from no records")
    except Exception as e:  # noqa: BLE001
        add("empty", False, f"{type(e).__name__}: {e}")

    if kind == "spans":
        starts = [s.get("metadata", {}).get("start_ns") for s in base]
        bad = [x for x in starts if x is not None and not (isinstance(x, int) and TS_MIN <= x <= TS_MAX)]
        add("timestamps", bool(starts) and any(x is not None for x in starts) and not bad,
            "no start times" if not any(x is not None for x in starts) else (f"{len(bad)} start time(s) not epoch ns" if bad else ""))
    elif kind == "records":
        ts = [r.get("time_ns") for r in base]
        bad = [x for x in ts if x is not None and not (TS_MIN <= x <= TS_MAX)]
        add("timestamps", not bad if any(x is not None for x in ts) else None, f"{len(bad)} time_ns out of range" if bad else "")
    else:
        add("timestamps", None, f"not applicable to {kind} sources")

    if spec.client_factory is None:
        add("pagination", None, "spec has no fake client for the live path")
        add("retries", None, "spec has no fake client for the live path")
    else:
        try:
            live = _canon(output(_build(spec, None, spec.client_factory(records, 2, 0)), spec, records))
            add("pagination", live == _canon(base), "" if live == _canon(base) else "live path (2 records per page) differs from fixture output")
        except Exception as e:  # noqa: BLE001
            add("pagination", False, f"{type(e).__name__}: {e}")
        try:
            live = _canon(output(_build(spec, None, spec.client_factory(records, 1000, 1)), spec, records))
            add("retries", live == _canon(base), "" if live == _canon(base) else "throttled call not retried")
        except Exception as e:  # noqa: BLE001
            add("retries", False, f"throttled first call not retried: {type(e).__name__}: {e}")
    return res


# ---- provider checks -------------------------------------------------------------
class ThrottlingException(Exception):
    """Stands in for any SDK's rate-limit error."""


def check_provider(spec: ProviderConformance) -> list[CheckResult]:
    from ..config import ProviderProfile
    from ..models import Message, ToolCall
    from ..plugins import load
    res: list[CheckResult] = []

    def add(check, ok, detail=""):
        res.append(CheckResult("provider", spec.provider, check, PASS if ok is True else (NOT_COVERED if ok is None else FAIL), detail))

    tools = [{"name": "lookup", "description": "look something up", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}]
    convo = [Message(role="system", content="sys"), Message(role="user", content="hi"),
             Message(role="assistant", content=None, tool_calls=[ToolCall(id="c1", name="lookup", args={"id": "X"})]),
             Message(role="tool", tool_call_id="c1", name="lookup", content='{"ok": true}')]
    cls = load("provider", spec.provider)

    def make(**over):
        p = cls(ProviderProfile(name="conf", provider=spec.provider, **{**spec.profile, **over}), spec.client_factory())
        p.retry_base_s = 0.0
        return p

    try:
        r = asyncio.run(make().complete(convo, tools, temperature=0))
        probs = []
        if r.text != spec.expect_text:
            probs.append(f"text {r.text!r} != {spec.expect_text!r}")
        u = r.usage
        if any(not isinstance(v, int) or v < 0 for v in (u.input_tokens, u.output_tokens)) or u.total <= 0:
            probs.append("usage must be non-negative ints with a positive total")
        if not r.model:
            probs.append("no model id")
        if r.latency_ms is None or r.latency_ms < 0:
            probs.append("no latency")
        add("normalisation", not probs, "; ".join(probs))
        if spec.expect_tool:
            ok = bool(r.tool_calls) and r.tool_calls[0].name == spec.expect_tool and isinstance(r.tool_calls[0].args, dict)
            add("tool_calls", ok, "" if ok else "tool call not parsed into name + dict args")
        else:
            add("tool_calls", None, "spec has no tool-call response")
    except Exception as e:  # noqa: BLE001
        add("normalisation", False, f"{type(e).__name__}: {e}")

    for check, exc, expect_calls in (("retries", ThrottlingException("ThrottlingException: rate exceeded"), 2),
                                     ("no_retry_on_client_error", ValueError("bad request"), 1)):
        p = make(retries=2)
        orig, calls = p._complete, []

        def flaky(*a, _orig=orig, _exc=exc, _calls=calls, **kw):
            _calls.append(1)
            if len(_calls) == 1:
                raise _exc
            return _orig(*a, **kw)
        p._complete = flaky
        try:
            asyncio.run(p.complete([Message(role="user", content="hi")]))
            ok = len(calls) == expect_calls
        except Exception:  # noqa: BLE001
            ok = check == "no_retry_on_client_error" and len(calls) == 1
        add(check, ok, "" if ok else f"{len(calls)} call(s)")

    try:
        asyncio.run(make().complete([Message(role="user", content="return json")], json_mode=True))
        add("json_mode", True)
    except Exception as e:  # noqa: BLE001
        add("json_mode", False, f"{type(e).__name__}: {e}")
    return res


# ---- running ---------------------------------------------------------------------
def run(specs: list[Any]) -> list[CheckResult]:
    out: list[CheckResult] = []
    for s in specs:
        out += check_source(s) if isinstance(s, SourceConformance) else check_provider(s)
    return out


def assert_conformant(spec: Any, allow_not_covered: bool = True) -> list[CheckResult]:
    """For partner test suites: raise AssertionError listing failed checks."""
    res = run([spec])
    bad = [r for r in res if r.status == FAIL or (r.status == NOT_COVERED and not allow_not_covered)]
    assert not bad, "conformance failures:\n" + "\n".join(f"  {r.adapter} / {r.check}: {r.status} {r.detail}" for r in bad)
    return res


def matrix_markdown(results: list[CheckResult]) -> str:
    sym = {PASS: "✓", FAIL: "✗", NOT_COVERED: "–"}
    lines = []
    for kind, checks in (("source", SOURCE_CHECKS), ("provider", PROVIDER_CHECKS)):
        rows = [r for r in results if r.kind == kind]
        if not rows:
            continue
        lines += [f"| {kind} | " + " | ".join(checks) + " |", "|---|" + "---|" * len(checks)]
        for a in sorted({r.adapter for r in rows}):
            by = {r.check: r.status for r in rows if r.adapter == a}
            lines.append(f"| `{a}` | " + " | ".join(sym.get(by.get(c, NOT_COVERED), "–") for c in checks) + " |")
        lines.append("")
    lines.append("✓ pass · ✗ fail · – not covered by the spec (never counted as a pass)")
    return "\n".join(lines) + "\n"


def builtin_specs() -> list[Any]:
    from .builtin import SPECS
    return SPECS
