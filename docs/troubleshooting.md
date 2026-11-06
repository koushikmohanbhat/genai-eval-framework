# Troubleshooting

## Configuration

| Symptom | Cause and fix |
|---|---|
| `environment variable(s) not set: X, Y` | the YAML references `${X}` without a default. Set it in the shell, `.env` or CI, delete the profile / source that needs it, or give a default (`${X:-value}`) |
| `invalid YAML ... expected ',' or '}'` | a `${VAR}` inside an inline mapping or list is unquoted. Write `{region: "${AWS_REGION}"}` |
| `.env` values are ignored | variables already set in the shell win over `.env`. Run `env | grep NAME`, or use `--env-file` for a different file |
| `put secrets in env vars or a secret manager` | a profile has an inline `api_key` / `token`. Use `auth.api_key_env` or `auth.api_key_secret` |
| `suite uses model graders but names no judges` | there is no default judge. Add `judges: [<profile>, <profile>]` |
| `judge ... same model family as target` | `judge_policy.require_different_family` is on. Use a judge from another family, or set `family:` on a profile whose family is detected wrongly (e.g. Claude on Bedrock) |
| `unknown profile 'x'` | the suite references a profile that isn't in the providers file in use. Check `--providers` / `GENAI_EVALS_PROVIDERS` and `EVAL_*_PROFILE` variables |
| `prompt 'x@1.2.0' not found` / `not in prompts.lock` | create the version folder, then `genai-evals prompts lock` |
| `changed after it was locked` | a released prompt version was edited. Copy the folder to a new version instead, and restore the old one |
| `promotion blocked ... adds data elements` | DPDP check: update the consent notice, ledger and DPDP eval suite, then promote with `--ack-dpdp` |
| gates fail with `untrusted grader` | the judge's calibration is below `min_judge_kappa`. Recalibrate with more labels or change judges |
| results look the same after a change | the run cache: unchanged cases are reused. Use `--no-cache` to force a rerun |
| `dataset too small to detect` warning | the paired comparison can't separate the difference from noise. Add cases or repeats |

## AWS

| Symptom | Cause and fix |
|---|---|
| `NoCredentialsError` / `ExpiredToken` | no valid credentials in the chain. `aws sso login`, check `AWS_PROFILE`, or the CI OIDC role |
| `AccessDeniedException` on `InvokeModel` | the role lacks `bedrock:InvokeModel` on that model (for inference profiles, on the profile **and** the underlying model ARNs), or model access is not granted in that region |
| `ValidationException ... model identifier is invalid` | the model id is not available in `AWS_REGION`; use an inference profile id if the model is only offered that way |
| `ThrottlingException` | lower `max_concurrency` / set `rpm` on the profile; transient errors are retried `retries` times |
| AgentCore source returns no samples | Transaction Search not enabled, spans in a different log group (set `AGENTCORE_SPANS_LOG_GROUP`), `service_name` filter doesn't match the agent's `service.name`, or the `window` is too short. Spans can take ~10 minutes to appear after enabling |
| samples have no prompts / outputs | span content capture is off. Enable Bedrock model invocation logging and join `aws_bedrock_invocation_logs` |
| AgentCore Runtime target returns raw JSON | set `options.output_key` (or `map`) to the field holding the answer |
| `PutMetricData` denied | the policy's `cloudwatch:namespace` condition must allow `GenAIEvals` |

## Google Cloud

| Symptom | Cause and fix |
|---|---|
| `DefaultCredentialsError` | run `gcloud auth application-default login`, or check the service account / WIF setup |
| `403 ... aiplatform.endpoints.predict` | grant `roles/aiplatform.user`; for Claude on Vertex also enable the model in Model Garden for that location |
| `404 ... model not found` | model not offered in `GCP_LOCATION`; some models are only on `global` or specific regions |
| `429 RESOURCE_EXHAUSTED` | quota: lower `max_concurrency` / set `rpm`, or request more quota |
| Cloud Trace source returns no samples | tracing not enabled on the agent (Agent Engine telemetry / `--trace_to_cloud`), Telemetry API not enabled, `service_name` filter mismatch, or the `window` is too short |
| samples have no prompts / outputs | message content capture is off. Turn on Vertex request-response logging and join `gcp_vertex_request_response` |
| BigQuery `Access Denied` | `roles/bigquery.jobUser` on the project **and** `roles/bigquery.dataViewer` on the dataset |
| one request appears as several traces | trace context didn't cross the Agent Engine boundary. The framework also groups by `session.id`; make sure sessions are set |

## OpenAI / Anthropic

| Symptom | Cause and fix |
|---|---|
| `401` | wrong key or wrong variable name in `api_key_env` |
| `429` | rate limit: lower `max_concurrency`, set `rpm`, or use a dedicated eval project with higher limits |
| `check-config` fails on `store: true` | set `options.store: false` on every OpenAI profile |
| usage source returns `403` | the usage APIs need an admin key (`admin_key_env`), not an inference key |

## Still stuck

Run with `genai-evals validate` first; it checks profiles, dataset, prompts, graders and gates without calling any
model. Open an issue with the command, the error and your (redacted) YAML.
