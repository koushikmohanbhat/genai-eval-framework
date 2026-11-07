"""Plugin contracts, adapter registry and the maturity catalog.

Everything cloud- or vendor-specific lives behind one of six adapter contracts. Suites, results and reports only
ever see the cloud-neutral shapes (`Sample`, `Score`, the result JSON), so a new adapter never changes them.

| Kind     | Contract (base class)                    | Selected by                           | Entry-point group          |
|----------|------------------------------------------|---------------------------------------|----------------------------|
| provider | `genai_evals.providers.base.Provider`    | `provider:` in a profile              | `genai_evals.providers`    |
| source   | `genai_evals.sources.base.Source`        | `type:` in sources.yaml               | `genai_evals.sources`      |
| target   | `genai_evals.targets.base.Target`        | `framework:` (agents) in a target     | `genai_evals.targets`      |
| grader   | `genai_evals.graders.base.Grader`        | `type:` in a grader                   | `genai_evals.graders`      |
| store    | `genai_evals.plugins.Store`              | URL scheme of `--store`               | `genai_evals.stores`       |
| report   | `genai_evals.plugins.ReportWriter`       | `--report-format` / `result export`   | `genai_evals.reports`      |

A partner package registers an adapter without forking:

    [project.entry-points."genai_evals.sources"]
    azure_monitor = "acme_evals.azure:AzureMonitorSource"

and proves it with the conformance kit (`genai-evals conformance`, see docs/plugins.md).

Maturity levels (lowest to highest):
  implemented        written to the documented API; no conformance or fixture tests
  fixture_tested     passes the conformance kit / recorded-response tests in this repo's CI
  live_tested        run against a live service (recorded in a project's validation.yaml)
  customer_validated validated in a customer environment and signed off (validation.yaml)
`experimental` is a separate flag for preview APIs whose request / response shapes may change.
"""
from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "1"
KINDS = ("provider", "source", "target", "grader", "store", "report")
LEVELS = ("implemented", "fixture_tested", "live_tested", "customer_validated")
GROUPS = {k: f"genai_evals.{k}s" for k in KINDS}


@dataclass
class AdapterInfo:
    kind: str
    name: str
    ref: str                                  # "module:attr"
    cloud: str = "neutral"                    # aws | gcp | openai | anthropic | neutral
    maturity: str = "implemented"
    experimental: bool = False
    origin: str = "builtin"                   # builtin | plugin:<distribution>
    notes: str = ""
    validation: list[dict[str, Any]] = field(default_factory=list)   # records from validation.yaml

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _a(kind, name, ref, cloud="neutral", maturity="fixture_tested", experimental=False, notes=""):
    return AdapterInfo(kind, name, ref, cloud, maturity, experimental, "builtin", notes)


P, S, T = "genai_evals.providers", "genai_evals.sources", "genai_evals.targets"
# Built-in adapters. `fixture_tested` entries are proven by tests/test_conformance.py and tests/test_providers.py;
# CI fails if an entry claims fixture_tested and has no passing conformance spec (sources) or tests (providers).
BUILTINS: list[AdapterInfo] = [
    _a("provider", "openai", f"{P}.openai_:OpenAIResponsesProvider", "openai", notes="Responses API; recorded-response tests"),
    _a("provider", "openai_compatible", f"{P}.openai_:OpenAICompatibleProvider", "neutral", notes="any Chat Completions endpoint / gateway"),
    _a("provider", "anthropic", f"{P}.anthropic_:AnthropicProvider", "anthropic"),
    _a("provider", "vertex_anthropic", f"{P}.anthropic_:AnthropicProvider", "gcp", notes="Claude on Vertex AI"),
    _a("provider", "bedrock", f"{P}.bedrock:BedrockConverseProvider", "aws", notes="Converse API, any Bedrock model"),
    _a("provider", "bedrock_anthropic", f"{P}.bedrock:BedrockConverseProvider", "aws"),
    _a("provider", "vertex", f"{P}.vertex:VertexGeminiProvider", "gcp", notes="Gemini via google-genai"),
    _a("provider", "mock", f"{P}.mock:MockProvider", "neutral", notes="offline demos and CI"),
    _a("source", "aws_agentcore_observability", f"{S}.aws:AgentCoreObservability", "aws"),
    _a("source", "aws_xray", f"{S}.aws:XRay", "aws"),
    _a("source", "aws_bedrock_invocation_logs", f"{S}.aws:BedrockInvocationLogs", "aws"),
    _a("source", "aws_agentcore_memory", f"{S}.aws:AgentCoreMemory", "aws"),
    _a("source", "aws_cloudwatch_metrics", f"{S}.aws:CloudWatchMetrics", "aws"),
    _a("source", "aws_agentcore_evaluations", f"{S}.aws:AgentCoreEvaluationsResults", "aws", experimental=True),
    _a("source", "gcp_cloud_trace", f"{S}.gcp:CloudTrace", "gcp"),
    _a("source", "gcp_cloud_logging", f"{S}.gcp:CloudLogging", "gcp"),
    _a("source", "gcp_vertex_request_response", f"{S}.gcp:VertexRequestResponse", "gcp"),
    _a("source", "gcp_agent_engine_sessions", f"{S}.gcp:AgentEngineSessions", "gcp"),
    _a("source", "gcp_cloud_monitoring", f"{S}.gcp:CloudMonitoring", "gcp"),
    _a("source", "gcp_vertex_eval_results", f"{S}.gcp:VertexEvalResults", "gcp", "implemented", True),
    _a("source", "otlp", f"{S}.other:OTLPFiles", "neutral"),
    _a("source", "openai_agents_trace", f"{S}.other:OpenAIAgentsTrace", "openai"),
    _a("source", "langfuse", f"{S}.other:Langfuse", "neutral"),
    _a("source", "openai_usage", f"{S}.other:OpenAIUsage", "openai", "implemented"),
    _a("source", "anthropic_usage", f"{S}.other:AnthropicUsage", "anthropic", "implemented"),
    _a("target", "tool_loop", f"{T}.agents:ToolLoopAgentTarget", "neutral", notes="provider-neutral tool-calling loop on any profile"),
    _a("target", "openai_agents_sdk", f"{T}.agents:OpenAIAgentsSDKTarget", "openai"),
    _a("target", "agentcore_runtime", f"{T}.agents:AgentCoreRuntimeTarget", "aws", "implemented"),
    _a("target", "bedrock_agent", f"{T}.agents:BedrockAgentTarget", "aws", "implemented"),
    _a("target", "agent_engine", f"{T}.agents:AgentEngineTarget", "gcp", "implemented"),
    _a("store", "sqlite", "genai_evals.store:ResultStore", "neutral"),
    _a("store", "postgresql", "genai_evals.store:ResultStore", "neutral", "implemented", notes="any SQLAlchemy URL (RDS, Aurora, Cloud SQL, AlloyDB)"),
    _a("store", "file", "genai_evals.store:FileStore", "neutral", notes="a folder of result JSON files; no database"),
    _a("report", "html", "genai_evals.report.writers:HtmlReport"),
    _a("report", "scorecard", "genai_evals.report.writers:ScorecardReport"),
    _a("report", "markdown", "genai_evals.report.writers:MarkdownReport"),
    _a("report", "junit", "genai_evals.report.writers:JUnitReport"),
    _a("report", "json", "genai_evals.report.writers:JsonReport"),
    _a("report", "csv", "genai_evals.report.writers:CsvScores"),
    _a("report", "jsonl", "genai_evals.report.writers:JsonlScores"),
    _a("report", "promptfoo", "genai_evals.report.writers:PromptfooResults"),
]


# ---- contracts not covered by an existing base class ---------------------------
class Store:
    """Results store contract. `url` is whatever followed `--store`; the scheme selects the store."""

    def __init__(self, url: str):
        self.url = url

    def save(self, result: dict[str, Any]) -> None:  # pragma: no cover - contract
        raise NotImplementedError

    def history(self, suite: str, limit: int = 20) -> list[dict[str, Any]]:  # pragma: no cover - contract
        raise NotImplementedError


class ReportWriter:
    """Report output contract: render a result (any schema version this framework reads) to one file."""
    name = "base"
    filename = "report.txt"
    binary = False

    def render(self, result: dict[str, Any]) -> str | bytes:  # pragma: no cover - contract
        raise NotImplementedError


# ---- registry -----------------------------------------------------------------
def _entry_points(group: str):
    from importlib.metadata import entry_points
    try:
        return list(entry_points(group=group))
    except TypeError:  # pragma: no cover - Python < 3.10 API
        return list(entry_points().get(group, []))


@lru_cache(maxsize=1)
def catalog() -> dict[tuple[str, str], AdapterInfo]:
    """Built-in adapters plus anything installed through entry points (plugins never override built-ins)."""
    out = {(a.kind, a.name): a for a in BUILTINS}
    for kind, group in GROUPS.items():
        for ep in _entry_points(group):
            if (kind, ep.name) in out:
                continue
            dist = getattr(getattr(ep, "dist", None), "name", None) or "unknown"
            info = AdapterInfo(kind, ep.name, ep.value, origin=f"plugin:{dist}")
            out[(kind, ep.name)] = info
    return out


def names(kind: str) -> list[str]:
    return sorted(n for k, n in catalog() if k == kind)


def info(kind: str, name: str) -> AdapterInfo | None:
    return catalog().get((kind, name))


def load(kind: str, name: str):
    """Import the adapter class. Plugin classes may declare `maturity`, `cloud`, `experimental`, `contract_version`."""
    a = info(kind, name)
    if a is None:
        raise KeyError(f"unknown {kind} '{name}' (available: {', '.join(names(kind))})")
    mod, _, attr = a.ref.partition(":")
    obj = getattr(importlib.import_module(mod), attr)
    if a.origin != "builtin":
        cv = str(getattr(obj, "contract_version", CONTRACT_VERSION))
        if cv.split(".")[0] != CONTRACT_VERSION:
            raise RuntimeError(f"{kind} plugin '{name}' targets contract {cv}; this framework provides {CONTRACT_VERSION}")
        a.maturity = getattr(obj, "maturity", a.maturity)
        a.cloud = getattr(obj, "cloud", a.cloud)
        a.experimental = bool(getattr(obj, "experimental", a.experimental))
    return obj


# ---- project validation records (live_tested / customer_validated) ----------------
def load_validation(path: str | Path | None) -> list[dict[str, Any]]:
    if not path or not Path(path).is_file():
        return []
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    recs = data.get("adapters", []) or []
    for r in recs:
        if r.get("status") not in LEVELS[2:]:
            raise ValueError(f"{path}: status must be live_tested or customer_validated (got {r.get('status')!r} for {r.get('name')})")
        if r.get("kind") not in KINDS or not r.get("name"):
            raise ValueError(f"{path}: each record needs kind ({'/'.join(KINDS)}) and name")
    return recs


def record_validation(path: str | Path, kind: str, name: str, status: str, *, environment: str = "", evidence: str = "",
                      by: str = "", when: str | None = None) -> dict[str, Any]:
    import yaml
    if status not in LEVELS[2:]:
        raise ValueError("status must be live_tested or customer_validated")
    if info(kind, name) is None:
        raise KeyError(f"unknown {kind} '{name}'")
    p = Path(path)
    data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    rec = {"kind": kind, "name": name, "status": status, "date": when or date.today().isoformat(), "by": by,
           "environment": environment, "evidence": evidence}
    data.setdefault("adapters", []).append(rec)
    p.write_text(VALIDATION_HEADER + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return rec


VALIDATION_HEADER = """\
# Adapter validation records for this project. Built-in maturity tops out at fixture_tested; add a record when an
# adapter has been run against a live service (live_tested) or validated and signed off in the customer's
# environment (customer_validated). Reports show the effective status of every adapter a run used.
#   genai-evals adapters record --kind source --name aws_agentcore_observability --status live_tested \\
#       --environment "customer sandbox, ap-south-1" --evidence out/run/result.json --by "partner name"
"""


def effective(kind: str, name: str, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    a = info(kind, name)
    base = a.maturity if a else "implemented"
    recs = [r for r in (records or []) if r["kind"] == kind and r["name"] == name]
    level = max([base] + [r["status"] for r in recs], key=LEVELS.index)
    return {"kind": kind, "name": name, "status": level, "builtin_status": base, "experimental": bool(a and a.experimental),
            "origin": a.origin if a else "unknown", "cloud": a.cloud if a else "unknown",
            "records": [{k: r.get(k) for k in ("status", "date", "environment", "by", "evidence")} for r in recs]}


def adapters_used(suite=None, providers=None, sources=None) -> list[tuple[str, str]]:
    """(kind, name) of every adapter a suite / sources config will touch."""
    used: set[tuple[str, str]] = set()
    if suite is not None and providers is not None:
        profs = {t.profile for t in suite.targets if t.profile} | set(suite.judges) | {g for gr in suite.graders for g in (gr.judges or [])}
        profs |= {n for n in (suite.simulator, suite.embeddings) if n}
        for n in profs:
            if n in providers.profiles:
                used.add(("provider", providers.profiles[n].provider))
        for t in suite.targets:
            if t.kind == "agent":
                used.add(("target", t.framework or "tool_loop"))
    if sources is not None:
        for s in sources:
            used.add(("source", s.type))
            for j in s.join or []:
                if j.get("type"):
                    used.add(("source", j["type"]))
    return sorted(used)


def markdown_table(records: list[dict[str, Any]] | None = None) -> str:
    """The maturity table in docs/adapters.md (regenerate with `genai-evals adapters --markdown`)."""
    lines = ["| Kind | Adapter | Cloud | Status | Notes |", "|---|---|---|---|---|"]
    for (k, n), a in sorted(catalog().items(), key=lambda x: (KINDS.index(x[0][0]), x[0][1])):
        e = effective(k, n, records)
        flag = " (experimental)" if a.experimental else ""
        lines.append(f"| {k} | `{n}` | {a.cloud} | {e['status']}{flag} | {a.notes} |")
    return "\n".join(lines) + "\n"
