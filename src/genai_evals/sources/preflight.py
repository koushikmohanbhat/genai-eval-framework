"""`genai-evals sources preflight`: before an online run, can the configured telemetry actually be graded?

Customers often have CloudWatch or Cloud Trace on without payload logging (prompts and completions), or the eval
role can't read it. For each source (and each join) this asks the cloud, cheaply, and sorts what it finds:

- ok:       the data is there and readable
- missing:  the logging isn't turned on, or the log group / table doesn't exist
- off:      spans arrive, but without prompt / completion content
- denied:   the eval identity lacks a permission (named)
- unknown:  an error we can't classify (shown as is)

Every non-ok finding carries the fix (the setting or permission) and the fallbacks when the customer can't or won't
turn logging on. Read-only: it never changes the customer's configuration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .base import build_source
from .normalize import spans_to_samples

FALLBACKS = [
    "emit the traces yourself: an OTLP exporter in the app with GenAI content capture on "
    "(OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true), read with the `otlp` source",
    "evaluate the endpoint instead of its telemetry: `genai-evals quickstart --url ...` now, a pack's targets/http.yaml "
    "later; wrap the agent with `@agent` / `@tool` (docs/connect-your-agent.md) so tool calls and state come back",
    "OpenAI Agents SDK apps: add genai_evals' trace processor (docs/sources.md) and read the `openai_agents_trace` source",
]

PERMS = {
    "aws_bedrock_invocation_logs": "bedrock:GetModelInvocationLoggingConfiguration, logs:DescribeLogGroups, "
                                   "logs:FilterLogEvents on the log group (or s3:ListBucket / s3:GetObject for S3 delivery)",
    "aws_agentcore_observability": "logs:DescribeLogGroups, logs:FilterLogEvents on aws/spans",
    "aws_xray": "xray:GetTraceSummaries, xray:BatchGetTraces",
    "gcp_vertex_request_response": "bigquery.tables.get and bigquery.tables.getData on the table "
                                   "(roles/bigquery.dataViewer) and bigquery.jobs.create (roles/bigquery.jobUser)",
    "gcp_cloud_trace": "cloudtrace.traces.list (roles/cloudtrace.user)",
    "gcp_cloud_logging": "logging.logEntries.list (roles/logging.viewer)",
}
ENABLE = {
    "aws_bedrock_invocation_logs": "turn on Bedrock model invocation logging with text delivery to that log group: "
                                   "aws bedrock put-model-invocation-logging-configuration --logging-config "
                                   "'{\"cloudWatchConfig\":{\"logGroupName\":\"<group>\",\"roleArn\":\"<role>\"},"
                                   "\"textDataDeliveryEnabled\":true}' (the customer's call: it stores prompts and "
                                   "completions; set retention and tokenise downstream)",
    "gcp_vertex_request_response": "enable request-response logging to BigQuery on the model or endpoint "
                                   "(setPublisherModelConfig / the endpoint's predictRequestResponseLoggingConfig), "
                                   "with a sampling rate; it stores prompts and completions",
    "aws_agentcore_observability": "enable AgentCore Observability (CloudWatch Transaction Search) for the runtime",
    "gcp_cloud_trace": "export spans to Cloud Trace (Telemetry API OTLP endpoint) from the agent",
}
CONTENT = ("spans arrive without prompts or completions: set OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true "
           "in the agent (or the AgentCore / ADK content setting), or join a payload source "
           "(aws_bedrock_invocation_logs, gcp_vertex_request_response)")


@dataclass
class Finding:
    source: str
    type: str
    status: str                                  # ok | missing | off | denied | unknown | skipped
    detail: str
    fix: str = ""
    fallbacks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped")


def classify(err: BaseException) -> str:
    """denied / missing / unknown from a boto3 or google-cloud error, without importing either."""
    code = ""
    resp = getattr(err, "response", None)
    if isinstance(resp, dict):
        code = str((resp.get("Error") or {}).get("Code", ""))
    name = type(err).__name__
    text = f"{code} {name} {err}".lower()
    if any(k in text for k in ("accessdenied", "unauthorized", "forbidden", "permissiondenied", "permission denied",
                               "not authorized", "403")):
        return "denied"
    if any(k in text for k in ("resourcenotfound", "notfound", "not found", "nosuch", "does not exist", "404")):
        return "missing"
    return "unknown"


def _err(spec, err: BaseException, what: str) -> Finding:
    kind = classify(err)
    t = spec.type
    if kind == "denied":
        return Finding(spec.name, t, "denied", f"{what}: permission denied ({type(err).__name__})",
                       f"grant the eval identity {PERMS.get(t, 'read access to this source')} "
                       "(docs/permissions.md); nothing else changes", FALLBACKS)
    if kind == "missing":
        return Finding(spec.name, t, "missing", f"{what}: not found ({err})",
                       ENABLE.get(t, "check the name, region / project and that the telemetry is switched on"), FALLBACKS)
    return Finding(spec.name, t, "unknown", f"{what}: {type(err).__name__}: {err}",
                   "check credentials, region / project and network access", FALLBACKS)


def _bedrock_logging(spec, src) -> Finding:
    group = src.opt("log_group", "/aws/bedrock/modelinvocations")
    try:
        c = src._client or __import__("boto3").client("bedrock", region_name=src.opt("region"))
        cfg = (src.call(c.get_model_invocation_logging_configuration) or {}).get("loggingConfig")
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, "Bedrock invocation logging configuration")
    if not cfg:
        return Finding(spec.name, spec.type, "missing", "Bedrock model invocation logging is off in this region",
                       ENABLE[spec.type], FALLBACKS)
    if not cfg.get("textDataDeliveryEnabled", False):
        return Finding(spec.name, spec.type, "off", "invocation logging is on but text delivery is off, so there are "
                       "no prompts or completions", "set textDataDeliveryEnabled: true in the logging configuration",
                       FALLBACKS)
    dest = (cfg.get("cloudWatchConfig") or {}).get("logGroupName")
    s3 = (cfg.get("s3Config") or {}).get("bucketName")
    if not src.opt("s3_uri") and dest and dest != group:
        return Finding(spec.name, spec.type, "missing", f"invocation logs go to {dest}, but sources.yaml reads {group}",
                       f"set log_group: {dest} in sources.yaml")
    if src.opt("s3_uri") and s3 and s3 not in src.opt("s3_uri"):
        return Finding(spec.name, spec.type, "missing", f"invocation logs go to s3://{s3}, but sources.yaml reads "
                       f"{src.opt('s3_uri')}", f"set s3_uri: s3://{s3}/... in sources.yaml")
    try:
        n = len(src.records())
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, f"reading {group}")
    if n == 0:
        return Finding(spec.name, spec.type, "missing", f"logging is on, but {group} has no records in the window",
                       "widen the window, or check the application calls Bedrock in this region", FALLBACKS)
    return Finding(spec.name, spec.type, "ok", f"logging on with text delivery; {n} records in the window")


def _vertex_logging(spec, src) -> Finding:
    table = src.opt("table", "").removeprefix("bq://")
    try:
        c = src._client
        if c is None:
            from google.cloud import bigquery  # type: ignore
            c = bigquery.Client(project=src.opt("project"))
        t = c.get_table(table)
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, f"BigQuery table {table}")
    cols = {f.name if hasattr(f, "name") else f.get("name") for f in (getattr(t, "schema", None) or [])}
    need = {src.cols["request"], src.cols["response"]}
    if cols and not need <= cols:
        return Finding(spec.name, spec.type, "missing", f"{table} has no {', '.join(sorted(need - cols))} column(s)",
                       "map the columns in sources.yaml (columns: {request: ..., response: ...})")
    try:
        n = len(src.records())
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, f"querying {table}")
    if n == 0:
        return Finding(spec.name, spec.type, "missing", f"{table} exists but has no rows in the window",
                       "check logging is enabled on the model / endpoint and its sampling rate", FALLBACKS)
    return Finding(spec.name, spec.type, "ok", f"{n} logged requests in the window")


def _spans(spec, src) -> Finding:
    try:
        raw = src.records()
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, "reading spans")
    if not raw:
        return Finding(spec.name, spec.type, "missing", "no spans in the window",
                       ENABLE.get(spec.type, "check the filter, window and that the agent exports traces"), FALLBACKS)
    samples = src.samples() if src.kind == "spans" else spans_to_samples(raw)
    with_out = sum(1 for s in samples if s.output)
    if samples and with_out == 0:
        joins = [j["type"] for j in spec.join]
        if joins:
            return Finding(spec.name, spec.type, "ok", f"{len(samples)} traces, no content on the spans; completions "
                           f"come from the join ({', '.join(joins)}), checked separately")
        return Finding(spec.name, spec.type, "off", f"{len(samples)} traces, none with a completion", CONTENT, FALLBACKS)
    return Finding(spec.name, spec.type, "ok", f"{len(samples)} traces, {with_out} with a completion")


CHECKS = {"aws_bedrock_invocation_logs": _bedrock_logging, "gcp_vertex_request_response": _vertex_logging}


def check_one(spec_dict: dict[str, Any] | Any, base_dir: str, client: Any = None) -> Finding:
    src = build_source(spec_dict, base_dir, client)
    spec = src.spec
    if spec.fixture:
        return Finding(spec.name, spec.type, "skipped", "reads a recorded fixture, not the live service")
    fn = CHECKS.get(spec.type)
    if fn:
        return fn(spec, src)
    if src.kind == "spans":
        return _spans(spec, src)
    try:
        src.records()
    except NotImplementedError:
        return Finding(spec.name, spec.type, "skipped", "no live check for this source type")
    except Exception as e:  # noqa: BLE001
        return _err(spec, e, "reading")
    return Finding(spec.name, spec.type, "ok", "readable")


def preflight(cfg, clients: dict[str, Any] | None = None) -> list[Finding]:
    clients = clients or {}
    out: list[Finding] = []
    for spec in cfg.sources:
        out.append(check_one(spec, cfg.base_dir, clients.get(spec.name)))
        for j in spec.join:
            name = j.get("name", f"{spec.name}:{j['type']}")
            out.append(check_one({"name": name, "window": spec.window, **j}, cfg.base_dir, clients.get(name)))
    return out


def render(findings: list[Finding]) -> str:
    L = []
    for f in findings:
        mark = {"ok": "ok  ", "skipped": "skip"}.get(f.status, f.status[:4].upper().ljust(4))
        L.append(f"[{mark}] {f.source} ({f.type}): {f.detail}")
        if f.fix:
            L.append(f"       fix: {f.fix}")
    fb = next((f.fallbacks for f in findings if f.fallbacks), None)
    if fb:
        L.append("\nIf the customer can't turn payload logging on (or not yet):")
        L += [f"  - {x}" for x in fb]
    return "\n".join(L)


def as_dicts(findings: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(f) | {"ok": f.ok} for f in findings]
