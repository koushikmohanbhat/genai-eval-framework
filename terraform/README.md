# Terraform: read-only access and scheduled online evals

Both modules run `genai-evals online` **inside the customer's own account / project** with least-privilege, read-only
access to telemetry, and write scores only as metrics.

| | AWS (`aws/`) | Google Cloud (`gcp/`) |
|---|---|---|
| Identity | ECS task role | service account |
| Reads | CloudWatch Logs (`aws/spans`, Bedrock invocation logs, extra groups), X-Ray, CloudWatch metrics, AgentCore Memory (optional) | Cloud Trace, Cloud Logging, Cloud Monitoring, BigQuery request-response dataset |
| Judges | optional `bedrock:InvokeModel / Converse` on listed model ARNs | optional `roles/aiplatform.user` |
| Writes | `cloudwatch:PutMetricData` limited to namespace `GenAIEvals` | `roles/monitoring.metricWriter` |
| Schedule | EventBridge → ECS Fargate task | Cloud Scheduler → Cloud Run job |
| Alarm | example CloudWatch alarm on the PII grader | add an alerting policy on `custom.googleapis.com/genai_eval/score` |

Build the image from the repo `Dockerfile` (suites in `/evals`), push it to ECR or Artifact Registry, then
`terraform init && terraform apply -var image_uri=... -var subnet_ids=[...] -var security_group_ids=[...]` (AWS) or
`-var project=... -var image=... -var request_response_dataset=...` (GCP).

The files are syntax-checked in development. Run `terraform validate` and `terraform plan` in your environment before applying.
