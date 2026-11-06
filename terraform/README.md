# Terraform: read-only access and scheduled online evals

Both modules run `genai-evals online` **inside the customer's own account / project**, with least-privilege,
read-only access to telemetry, and write scores only as metrics. They have no environment-specific defaults: every
region, project, image and log group is a variable you set in your own `terraform.tfvars` (not committed here).

| | AWS (`aws/`) | Google Cloud (`gcp/`) |
|---|---|---|
| Identity | ECS task role | service account |
| Reads | CloudWatch Logs (spans, Bedrock invocation logs, extra groups), X-Ray, CloudWatch metrics, AgentCore Memory (optional) | Cloud Trace, Cloud Logging, Cloud Monitoring, BigQuery request-response dataset |
| Judges | optional `bedrock:InvokeModel` on listed model ARNs | optional `roles/aiplatform.user` |
| Writes | `cloudwatch:PutMetricData` limited to namespace `GenAIEvals` | `roles/monitoring.metricWriter` |
| Schedule | EventBridge → ECS Fargate task | Cloud Scheduler → Cloud Run job |
| Alarm | example CloudWatch alarm on the PII grader | add an alerting policy on `custom.googleapis.com/genai_eval/score` |

## Steps

1. **Turn on the telemetry** the sources read: [AWS](../docs/setup-aws.md#3-turn-on-the-telemetry-online-evals-read),
   [Google Cloud](../docs/setup-gcp.md#3-turn-on-the-telemetry-online-evals-read).
2. **Build your image.** The framework image contains no suites or settings. Build it from this repo's
   `Dockerfile`, then a derived image with your evals project:

   ```dockerfile
   FROM <framework image>
   COPY evals/ /evals/          # sources.yaml, online_checks.yaml, providers.live.yaml, prompts/
   ```

   Push it to ECR (AWS) or Artifact Registry (Google Cloud) in the customer's account / project.
3. **Fill in variables**: `cp terraform.tfvars.example terraform.tfvars` in `aws/` or `gcp/` and set the values.
   Put the `${VAR}` values your `sources.yaml` and `providers.live.yaml` read (service name, log groups, BigQuery
   table, judge model ids) in the `environment` map. Keys stay in a secret manager and are referenced from
   `providers.live.yaml` with `api_key_secret`.
4. **Apply**: `terraform init && terraform validate && terraform plan && terraform apply`. Keep the state in the
   customer's backend (S3 / GCS), not in this repo.
5. **Verify** the first run (ECS task logs / Cloud Run job execution), then the published scores, then add alarms
   for your gates.

The modules are syntax-checked in development but have not been applied to a live account by this project yet:
review the plan before applying.

## Variables

**AWS** (`aws/main.tf`): `region` (required), `image_uri` (required), `subnet_ids`, `security_group_ids`
(required), `environment` (map), `name`, `schedule`, `spans_log_group`, `invocation_log_group`,
`extra_log_group_arns`, `agentcore_memory_arns`, `judge_model_arns`, `command`.

**Google Cloud** (`gcp/main.tf`): `project`, `region`, `image`, `request_response_dataset` (required),
`environment` (map), `name`, `schedule`, `use_vertex_judges`.
