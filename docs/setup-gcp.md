# Google Cloud setup

What a partner needs in the customer's Google Cloud project to (1) evaluate Gemini and Claude-on-Vertex models and
Vertex AI Agent Engine agents, and (2) grade production traffic from Cloud Trace, Cloud Logging, BigQuery and Cloud
Monitoring. Projects, locations and table names go in your `.env` or your CI / container environment, not the YAML.

Install the extra: `pip install "genai-eval-framework[gcp] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@v0.1.0"`
(add `anthropic` for Claude on Vertex).

## 1. Credentials

The framework uses Application Default Credentials (ADC). Don't put service-account keys in the repo.

| Where it runs | How it authenticates |
|---|---|
| Laptop | `gcloud auth application-default login` (optionally `--impersonate-service-account=<sa>`) |
| GitHub Actions | Workload Identity Federation: `google-github-actions/auth` with `workload_identity_provider` and `service_account` |
| Cloud Build / Cloud Run | the service account the job runs as |

Set `GCP_PROJECT` and `GCP_LOCATION` and reference them as `project: "${GCP_PROJECT}"`, `location: "${GCP_LOCATION}"`.

## 2. APIs and roles for offline runs (targets and judges)

```bash
gcloud services enable aiplatform.googleapis.com --project "$GCP_PROJECT"
# only if you use them:
gcloud services enable secretmanager.googleapis.com monitoring.googleapis.com --project "$GCP_PROJECT"
```

| Feature | Role (on the project unless noted) |
|---|---|
| Gemini targets, judges, simulator, embeddings | `roles/aiplatform.user` |
| Claude on Vertex (`vertex_anthropic`) | `roles/aiplatform.user`, and the model enabled in Model Garden for the location you use |
| Agent Engine target | `roles/aiplatform.user` |
| `vertex_metric` grader (Gen AI evaluation service) | `roles/aiplatform.user` |
| `api_key_secret: gcp-sm://...` | `roles/secretmanager.secretAccessor` on the secret |
| `--publish cloud_monitoring` | `roles/monitoring.metricWriter` |

## 3. Turn on the telemetry online evals read

### Traces in Cloud Trace (`gcp_cloud_trace`)

- **Agent Engine (ADK)**: deploy with tracing on (`adk deploy agent_engine ... --trace_to_cloud`, or set
  `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` on the deployment).
- **Agents running elsewhere** (Cloud Run, GKE, on-premises): send OTLP to the **Telemetry API**:

  ```bash
  gcloud services enable telemetry.googleapis.com cloudtrace.googleapis.com logging.googleapis.com monitoring.googleapis.com --project "$GCP_PROJECT"
  ```

  and point the OTLP exporter at `https://telemetry.googleapis.com` (or run the collector in
  [`collector/gcp-otelcol.yaml`](../collector/gcp-otelcol.yaml), which redacts Indian identifiers before export).
- Use the OpenTelemetry GenAI semantic conventions (the Google instrumentations do), so spans show in Trace
  Explorer's Generative AI view and the framework can read model calls and tool calls.
- **Message content** is off by default (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`). Leave it off
  if the customer prefers, and use request-response logging below for prompts and completions. If you turn it on,
  remember that traces then hold personal data.
- Set `filter.service_name` to the agent's OTel `service.name` (`AGENT_SERVICE_NAME`).

### Vertex request-response logging to BigQuery (`gcp_vertex_request_response`)

Records the exact prompts and completions of Gemini (and supported partner models such as Claude) in a BigQuery table.
Turn it on per model with `setPublisherModelConfig` (REST) or, in the Vertex AI SDK,
`GenerativeModel(<model>).set_request_response_logging_config(enabled=True, sampling_rate=<0..1>, bigquery_destination="bq://<project>.<dataset>.<table>")`.
Set `VERTEX_REQUEST_RESPONSE_TABLE=bq://<project>.<dataset>.<table>`. The table holds personal data: restrict
access, set table expiration, and keep the framework's tokenisation on.

### Optional sources

| Source | Turn on | Variable |
|---|---|---|
| `gcp_agent_engine_sessions` (conversation history) | the agent uses Agent Engine Sessions | `AGENT_ENGINE_RESOURCE` |
| `gcp_cloud_logging` | the agent writes structured logs (stdout or the logging client) | — |
| `gcp_cloud_monitoring` | nothing (Vertex AI and Agent Engine publish metrics) | — |
| `gcp_vertex_eval_results` | Gen AI evaluation service runs | — |

## 4. Read-only roles for online evals

| Role | Why |
|---|---|
| `roles/cloudtrace.user` | read traces (including spans ingested through the Telemetry API) |
| `roles/logging.viewer` | Cloud Logging and Agent Engine logs |
| `roles/monitoring.viewer` | Vertex AI and Agent Engine metrics |
| `roles/bigquery.jobUser` (project) + `roles/bigquery.dataViewer` (on the request-response dataset only) | query request-response logs |
| `roles/aiplatform.viewer`, or `roles/aiplatform.user` if judges run on Vertex | Agent Engine sessions / Vertex judges |
| `roles/monitoring.metricWriter` | publish `custom.googleapis.com/genai_eval/*` scores |

[`terraform/gcp`](../terraform/README.md) creates a service account with exactly these roles.

## 5. Schedule online evals in the customer's project

1. Build an image that contains **your** evals project (the framework image ships none):

   ```dockerfile
   FROM <framework image built from this repo's Dockerfile>
   COPY evals/ /evals/
   ```

   Push it to Artifact Registry in the customer's project.
2. Copy `terraform/gcp/terraform.tfvars.example` to `terraform.tfvars`, fill it in (project, region, image,
   request-response dataset, and the `environment` map with the `${VAR}` values your `sources.yaml` reads), then
   `terraform init && terraform plan && terraform apply`.
3. Check the first Cloud Run job execution, then the scores in Cloud Monitoring under
   `custom.googleapis.com/genai_eval/score`. Add alerting policies for your gates.

## 6. Verify

```bash
gcloud auth application-default print-access-token > /dev/null && echo "ADC ok"
gcloud services list --enabled --project "$GCP_PROJECT" | grep -E "aiplatform|telemetry|cloudtrace|bigquery"
bq show "${VERTEX_REQUEST_RESPONSE_TABLE#bq://}"                      # table exists (use project:dataset.table form if needed)
genai-evals validate suite.yaml --providers providers.live.yaml
genai-evals online --sources sources.yaml --suite online_checks.yaml --publish emf     # read-only dry run
```

Problems: see [troubleshooting](troubleshooting.md#google-cloud).

References: [Instrument an agent with OpenTelemetry (Telemetry API, GenAI view)](https://docs.cloud.google.com/stackdriver/docs/instrumentation/ai-agent-langgraph) ·
[Vertex request-response logging](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/request-response-logging) ·
[Agent Engine overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reasoning-engine/use)
