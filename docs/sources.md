# Native telemetry sources, AWS ↔ Google Cloud parity, and OpenTelemetry

Online evals read what production already records. You don't have to re-instrument anything. Connectors are
**read-only** and run **in the customer's own AWS account or Google Cloud project**. Every connector accepts a
`fixture:` path, which reads recorded records instead of calling the API (used by the demo and CI).

## Connector kinds

| Kind | Purpose | Connectors |
|---|---|---|
| spans | primary: produce samples | `aws_agentcore_observability`, `aws_xray`, `gcp_cloud_trace`, `otlp`, `openai_agents_trace`, `langfuse` |
| records | join: prompts / outputs / usage when span content is missing; can also be primary | `aws_bedrock_invocation_logs`, `gcp_vertex_request_response`, `gcp_cloud_logging` |
| history | join: conversation history per session | `aws_agentcore_memory`, `gcp_agent_engine_sessions` |
| scores | import managed evaluator results | `aws_agentcore_evaluations`, `gcp_vertex_eval_results` |
| metrics | operational baselines in the report | `aws_cloudwatch_metrics`, `gcp_cloud_monitoring`, `openai_usage`, `anthropic_usage` |

Joins match on trace id, then session id, then time window plus model.

## What online scores mean

Online evals are **monitoring**, not release evidence. The report and scorecard say so ("Production monitoring") and
show, per source, how many sessions were graded out of how many were read, the sampling rate and the window.

- **Reference-free.** There are no expected answers for production traffic, so only reference-free graders run
  (policy, PII, faithfulness to retrieved context, trajectory sanity, ops). A score is a signal to alert on and
  investigate, not a correctness rate.
- **Sampling.** `sampling` keeps a session (or trace, when there is no session id) by a hash of its id, so the same
  session is always in or out across runs and joins, independent of arrival order. Whole sessions are kept, never
  half of one.
- **Selection bias.** Only traffic the telemetry captured can be graded: sessions dropped by trace sampling, filtered
  by log settings, or without content capture (and no join to recover it) are missing, and they may differ from the
  ones you see. Compare the graded population with the operational metrics in the same report. Set `capture_rate`
  when the capture is a known random share, so `cases sample` can weight back to production; it cannot correct
  capture that drops sessions for a reason (errors, a channel without instrumentation).
- **Window effects.** A 24-hour window over-represents the hours with most traffic. Compare like with like (same window
  length and weekday) when you look at trends.

## Troubleshooting telemetry joins

Traces (CloudWatch / X-Ray, Cloud Trace) say *that* a model was called; the prompt and completion usually live
elsewhere: Bedrock model invocation logs, or the Vertex request-response table in BigQuery. Both are **off by
default**, and both are joined to traces after the fact. If the join silently fails, online scores grade empty or
wrong outputs.

```bash
genai-evals sources check sources.yaml          # reads the window, grades nothing; exit 1 on problems
```

```
claims-agent-aws (aws_agentcore_observability): 8 samples, 8 with prompt, 8 with completion
  + aws_bedrock_invocation_logs: 8 records (8 with trace id); matched trace_id=8 session_id=0 timestamp=0 none=0; ambiguous=0 disagreeing=0 duplicates dropped=0
    trace_id    trace e2cbbb93d9814559 -> 1 record(s), last req-66ee8f4ecb4f
      prompt:     Hi, car hit near Indiranagar today, policy MOT-2026-004512.
      completion: I've registered claim CLM-0001 for the collision. A surveyor will contact you within 24 hours.
      span says:  I've registered claim CLM-0001 for the collision. A surveyor will contact you within 24 hours.
  ok
```

**Read the pairs.** A match says a record was *associated* with a span, not that it is the right prompt and
completion for it. The check prints a few matched pairs (`--pairs N`, identifiers tokenised, text truncated) for a
person to read, and also flags pairings that can't all be right:

- **ambiguous:** one payload record matched by more than one span (overlapping time windows, or reused ids);
- **disagreeing:** the span captured its own completion and the joined record's completion is different;
- **duplicates dropped:** the same request id read twice (for example, overlapping windows), kept once so usage
  isn't double-counted;
- **session matches outside the window:** a session has many turns, so a session-id match only counts when the
  record falls in the span's own time window and doesn't name a different trace.

Each span is matched to a payload record by **trace id**, then **session id**, then **timestamp** (within 2 s, same
model). Timestamp matches are fuzzy: with overlapping sessions they can pair the wrong prompt with a trace, so the
check fails when more than 10% of spans match that way (`--max-time-share`) or fewer than 90% match by an id
(`--min-id-share`). `genai-evals doctor` runs the same diagnosis on the latest live online result
(**telemetry joins**), and every online result records it per source under `join:<type>` and `content`.

**Online runs enforce it.** By default a run whose joins fail the check is marked **unreliable**: every gate on the
affected target is *invalid* (so the run exits 1), the scorecard shows *incomplete* for that target, and the report
lists the problems. A run with questionable joins is never presented as a normal scorecard. Set the thresholds, or
report without blocking, in `sources.yaml`:

```yaml
join_policy: {on_problem: invalid, min_id_share: 0.9, max_time_share: 0.1, min_content_share: 0.9}   # or on_problem: warn
```

The result records `telemetry_quality: {status: ok | unreliable | questionable, problems}`.

### Checking the request tags on a real call

Passing ids in request tags is an instrumentation change in the agent, so check it once per provider before relying
on it. [`examples/request-tags/`](../examples/request-tags) makes one tagged call and prints its trace id:

```bash
python examples/request-tags/bedrock_converse.py        # or vertex_generate.py
genai-evals sources check sources.yaml --trace <trace id>
```

`--trace` says whether that trace was *matched by trace_id*, whether the payload log carries it but no sampled span
does (the script's own span isn't exported), or whether it is in neither.

| Provider | Where the ids go | Status |
|---|---|---|
| Bedrock (Converse / ConverseStream) | `requestMetadata` map in the request body; InvokeModel uses the `X-Amzn-Bedrock-Request-Metadata` header | Documented by AWS: recorded under the invocation log's top-level `requestMetadata` when invocation logging is on in that Region. Up to 16 entries, 256 characters each. Not yet run live by us |
| Vertex (generateContent / streamGenerateContent) | request `labels` (keys start with a lowercase letter; lowercase letters, digits, `_`, `-`, up to 63 characters) | Labels are a documented request field for billing. **Unverified:** that the request-response log keeps them in `full_request`. If not, map trace and session columns with `columns:` on the source |

| Problem | Fix |
|---|---|
| no payload records in the window | AWS: turn on Bedrock model invocation logging with text delivery to the log group in `sources.yaml`. GCP: enable request-response logging on the model or endpoint (it has its own sampling rate). Check the window and read permissions |
| records carry no trace id | Bedrock invocation logs don't carry the trace: pass `requestMetadata={"trace_id": ..., "session_id": ...}` on each Converse call. Vertex: add `labels={"trace_id": ..., "session_id": ...}` to each request. A W3C `traceparent` value works too |
| spans with no completion | turn on content capture in the agent's instrumentation (GenAI semantic conventions: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`), or join a payload source |
| many unmatched spans | the payload log's sampling rate is lower than the trace sampling rate, or the windows differ |

## AWS ↔ Google Cloud equivalents

| Capability | AWS | Google Cloud | How the framework uses it |
|---|---|---|---|
| OTLP trace ingestion | ADOT → CloudWatch `aws/spans` (Transaction Search), X-Ray | **Telemetry API** (`telemetry.googleapis.com`) → Cloud Trace | read spans; emit eval spans to the same backend |
| GenAI trace view | AgentCore Observability dashboard | Cloud Trace Explorer *Generative AI* view; Agent Engine traces | report deep links to the trace |
| Managed agent runtime telemetry | AgentCore Runtime | Agent Engine (`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY`, `adk deploy --trace_to_cloud`) | zero-touch source |
| Prompt / response capture | Bedrock model invocation logging (CloudWatch / S3) | Vertex request-response logging → **BigQuery**; `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (off by default) | fill missing content |
| Logs | CloudWatch Logs / Logs Insights | Cloud Logging / Log Analytics | bulk pulls |
| Metrics | CloudWatch (`AWS/Bedrock`, AgentCore) | Cloud Monitoring (Vertex AI, Agent Engine) | baselines in, scores out |
| Alerts / dashboards | CloudWatch alarms / dashboards | Cloud Monitoring alerting / dashboards | alarm on `GenAIEvals/Score` or `genai_eval/score` |
| Session / memory history | AgentCore Memory | Agent Engine Sessions / Memory Bank | multi-turn context |
| Managed evaluation | AgentCore Evaluations | Gen AI evaluation service | import; optional delegation |
| Safety signals | Bedrock Guardrails trace | Gemini safety ratings, Model Armor | `guardrail_triggered` grader |
| Prompt management | Bedrock Prompt Management | Vertex AI prompt management | `prompts push` mirror |
| Read-only access | IAM policy in `terraform/aws` | IAM roles in `terraform/gcp` | least privilege |

## OpenTelemetry strategy

- **One schema.** Spans are normalised to the OTel GenAI semantic conventions: `gen_ai.operation.name`, model,
  `gen_ai.usage.*`, `gen_ai.input/output.messages` (current) and `gen_ai.*.message` events (older), and
  `gen_ai.tool.*`. OpenInference attributes (`llm.*`, `retrieval.documents.*`, `openinference.span.kind`) and
  AgentCore / ADK span names are mapped too. The shapes accepted are OTLP JSON, CloudWatch span records, X-Ray
  segments, Cloud Trace spans, Agents SDK exports and Langfuse observations.
- **Prompt version on every span.** `genai_evals.prompts.load()` adds `prompt.id`, `prompt.version` and `prompt.hash`
  to the current span, so online scores can be split by prompt version.
- **Eval runs are OTel too.** Spans `genai_eval.run > genai_eval.case > genai_eval.grade` are linked to the graded
  production trace. Metrics: `genai_eval.score`, `genai_eval.pass`, `genai_eval.judge_cost`.
- **Collectors.** Configs for the ADOT collector and the Google-built OpenTelemetry Collector are in `collector/`, and
  both redact Indian identifiers before export.
- **Gaps handled.** Conventions are still evolving, so missing attributes are tolerated. Content capture is often off,
  so content is taken from request-response logs. Trace context may break at a managed-runtime boundary, so spans are
  also grouped by `session.id`. Cross-cloud calls are joined by W3C `traceparent` when it propagates.

## Example

Every account-specific value is an environment variable (see [configuration](configuration.md#environment-variables)).

```yaml
sources:
  - name: claims-agent-aws
    type: aws_agentcore_observability
    region: "${AWS_REGION}"
    log_group: "${AGENTCORE_SPANS_LOG_GROUP:-aws/spans}"
    filter: {service_name: "${AGENT_SERVICE_NAME}", agent_arn: "${AGENTCORE_AGENT_ARN}"}
    window: {last: 24h}
    sampling: 0.05
    capture_rate: 1.0          # the agent's trace sampler keeps every session (see growing-the-eval-set.md#weights-from-the-sample-back-to-production)
    group_by: session
    join:
      - {type: aws_bedrock_invocation_logs, log_group: "${BEDROCK_INVOCATION_LOG_GROUP}"}
      - {type: aws_agentcore_memory, memory_id: "${AGENTCORE_MEMORY_ID}", actor_id: default}
      - {type: aws_agentcore_evaluations, log_group: "${AGENTCORE_EVAL_RESULTS_LOG_GROUP}"}
      - {type: aws_cloudwatch_metrics, dimensions: {ModelId: "${BEDROCK_MODEL_ID}"}}
  - name: claims-agent-gcp
    type: gcp_cloud_trace
    project: "${GCP_PROJECT}"
    filter: {service_name: "${AGENT_SERVICE_NAME}"}
    join:
      - {type: gcp_vertex_request_response, table: "${VERTEX_REQUEST_RESPONSE_TABLE}",
         columns: {request: full_request, response: full_response, time: logging_time, model: model}}
      - {type: gcp_agent_engine_sessions, resource: "${AGENT_ENGINE_RESOURCE}", location: "${GCP_LOCATION}"}
      - {type: gcp_cloud_monitoring, project: "${GCP_PROJECT}"}
```

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --publish cloudwatch --region "$AWS_REGION"
genai-evals online --sources sources.yaml --suite online_checks.yaml --publish cloud_monitoring --project "$GCP_PROJECT"
```

What has to be switched on in each cloud before these sources return data is in [AWS setup](setup-aws.md) and
[Google Cloud setup](setup-gcp.md).

## OpenAI Agents SDK

```python
from agents import add_trace_processor
from genai_evals.sources import GenAIEvalsTraceProcessor
add_trace_processor(GenAIEvalsTraceProcessor("traces/agents.jsonl"))
```
Then use `{type: openai_agents_trace, path: traces/agents.jsonl}` as a source.
