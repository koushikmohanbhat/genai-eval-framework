# Permissions (for the customer's security review)

What the [Terraform modules](../terraform/README.md) grant, why, and what the identities can't do. The evals run in
the customer's own account or project; nothing is granted to the partner. Hand this page to the security team with
the `terraform plan` output.

**In short:** read-only access to the telemetry the online checks grade, model calls only for the judges you list,
and writing scores as metrics. No identity here can change the agent, its prompts, its data, its IAM or its
infrastructure, and none can read secrets.

## AWS (`terraform/aws`)

**Task role** (`<name>-task`, assumed only by ECS tasks), the identity that runs `genai-evals online`:

| Permission | Scope | Why | Can't |
|---|---|---|---|
| `logs:FilterLogEvents`, `GetLogEvents`, `StartQuery`, `GetQueryResults`, `DescribeLogGroups` | the spans log group, the Bedrock invocation log group and any `extra_log_group_arns` you list | read agent spans (`aws/spans`) and the Bedrock prompts and completions they're joined to | read any other log group; write or delete logs |
| `xray:GetTraceSummaries`, `BatchGetTraces` | `*` (X-Ray has no resource-level permissions) | read traces when the X-Ray source is used | write traces or change sampling rules |
| `cloudwatch:GetMetricData` | `*` (no resource-level permissions) | operational baselines (latency, errors, tokens) | change alarms or dashboards |
| `bedrock-agentcore:ListEvents`, `GetEvent` | only the AgentCore Memory ARNs you list (omitted when none) | conversation history for multi-turn checks | write or delete memory |
| `bedrock:InvokeModel` | only the judge model ARNs you list (omitted when none) | LLM judges on Bedrock (Converse is authorised by this action) | call any other model; create or change models, guardrails or agents |
| `cloudwatch:PutMetricData` | condition: namespace `GenAIEvals` only | publish scores so they can be alarmed on | write metrics in any other namespace |

**Execution role** (`<name>-exec`): the AWS-managed `AmazonECSTaskExecutionRolePolicy` (pull the image, write the
job's own logs). **Scheduler role** (`<name>-events`, assumed only by EventBridge): `ecs:RunTask` on this task
definition and `iam:PassRole` on these two roles only.

## Google Cloud (`terraform/gcp`)

**Service account** `<name>@...` that runs the Cloud Run job:

| Role | Scope | Why | Can't |
|---|---|---|---|
| `roles/cloudtrace.user` | project | read traces, including spans sent through the Telemetry API | write traces |
| `roles/logging.viewer` | project | Cloud Logging GenAI events and Agent Engine logs | read private (data access) logs; write or delete logs |
| `roles/monitoring.viewer` | project | Vertex AI and Agent Engine metrics | change alerting policies or dashboards |
| `roles/monitoring.metricWriter` | project | publish `custom.googleapis.com/genai_eval/*` scores | read data; change alerting. IAM can't limit it to one metric prefix |
| `roles/bigquery.jobUser` | project | run the read query against the request-response table | read any table on its own (needs dataViewer on the dataset) |
| `roles/bigquery.dataViewer` | the request-response **dataset** only | read Vertex prompts and completions | read other datasets; write or delete tables |
| `roles/aiplatform.viewer` | project | read Agent Engine sessions and evaluation results | create, change or delete any Vertex resource; call models |
| custom `<name>_judge`: `aiplatform.endpoints.predict` | project (only when `use_vertex_judges`) | LLM judges on Vertex (generateContent, and rawPredict for partner models) | deploy, create or delete endpoints, datasets, jobs or models. Earlier versions granted `roles/aiplatform.user`, which can |

**Scheduler service account** (`<name>-sched`): `roles/run.invoker` on this Cloud Run job only.

## What else to check

- **Data.** Judges receive tokenised text (structured identifiers replaced; see [DPDP](dpdp.md)), but free text
  still reaches the judge model. Pin judges to in-region profiles if data must stay in a region.
- **Secrets.** API keys for OpenAI or Anthropic judges come from the customer's secret manager, referenced in
  `providers.live.yaml`; none are in the images, the Terraform or the repository.
- **Outbound network.** The job calls the cloud APIs above and, when configured, the OpenAI or Anthropic API.
- **The image.** Built by the customer from this repository's Dockerfile plus their evals folder; every release
  attaches the wheel with build provenance and an SBOM (`gh attestation verify`).
- **CI.** The pack's CI template uses OIDC (no stored cloud keys) and actions pinned to commit SHAs.

Status: the modules pass `terraform validate` in CI; they have not been applied to a live account by this project.
