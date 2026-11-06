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
