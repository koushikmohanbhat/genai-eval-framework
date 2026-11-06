# AWS setup

What a partner needs in the customer's AWS account to (1) evaluate Bedrock models and AgentCore / Bedrock agents,
and (2) grade production traffic from CloudWatch, X-Ray and AgentCore. Nothing here is stored in the framework:
regions, ARNs and log groups go in your `.env` or your CI / container environment.

Install the extra: `pip install "genai-eval-framework[aws] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@v0.1.0"`.

## 1. Credentials

The framework uses the standard AWS credential chain (boto3). Don't put keys in YAML or `.env`.

| Where it runs | How it authenticates |
|---|---|
| Laptop | `aws sso login --profile <profile>`, then `AWS_PROFILE=<profile>` (or `auth: {aws_profile: ...}` in a profile) |
| GitHub Actions | OIDC: `aws-actions/configure-aws-credentials` with `role-to-assume` (no long-lived keys) |
| CodeBuild / ECS / Lambda | the service role or task role |
| Cross-account | `auth: {role_arn: ...}` on the Bedrock profile; the framework calls `sts:AssumeRole` |

Set `AWS_REGION` (and reference it as `region: "${AWS_REGION}"` in profiles and sources).

## 2. Permissions for offline runs (targets and judges)

Grant only the rows you use, scoped to the specific resources.

| Feature | IAM actions | Resource |
|---|---|---|
| Bedrock models as targets, judges, simulator (Converse API) | `bedrock:InvokeModel` | foundation-model ARNs, and inference-profile ARNs plus their underlying model ARNs |
| Bedrock embeddings (`embedding_similarity`) | `bedrock:InvokeModel` | the embedding model ARN |
| Bedrock Guardrails on a profile | `bedrock:ApplyGuardrail` | the guardrail ARN |
| AgentCore Runtime target | `bedrock-agentcore:InvokeAgentRuntime` | the agent runtime ARN (and endpoint) |
| Bedrock Agents target | `bedrock:InvokeAgent` | the agent alias ARN |
| Bedrock Knowledge Bases retriever | `bedrock:Retrieve` (and `bedrock:RetrieveAndGenerate` if used) | the knowledge base ARN |
| `agentcore_evaluator` grader | the AgentCore Evaluations `Evaluate` action (check the exact name in the IAM service authorization reference for `bedrock-agentcore`) | the evaluator |
| `api_key_secret: aws-sm://...` | `secretsmanager:GetSecretValue` | the secret ARN |
| `--publish cloudwatch` | `cloudwatch:PutMetricData` with condition `cloudwatch:namespace = GenAIEvals` | `*` |
| `prompts push --to bedrock --apply` | `bedrock:ListPrompts`, `bedrock:CreatePrompt`, `bedrock:UpdatePrompt`, `bedrock:CreatePromptVersion` | prompts |

Model access: confirm in the Bedrock console that the account can invoke each model you list (some providers
require a one-time access request), in the region you set.

## 3. Turn on the telemetry online evals read

Online evals don't instrument anything. They read what the customer's agents already emit, so these must be on:

### AgentCore Observability spans (`aws_agentcore_observability`)

1. **Enable CloudWatch Transaction Search** (one-time per account): CloudWatch console → *Application Signals (APM)* →
   *Transaction search* → *Enable Transaction Search* → tick *ingest spans as structured logs* → *Save*. It can take
   about ten minutes before spans are searchable. The API route is in the AgentCore observability guide.
2. **Agents on AgentCore Runtime** emit spans through the AWS Distro for OpenTelemetry (ADOT). Add the ADOT SDK to
   the agent code as the AgentCore observability guide describes.
3. **Agents hosted elsewhere** (ECS, EKS, Lambda, on-premises) need ADOT plus the environment variables in the
   AgentCore guide (`AGENT_OBSERVABILITY_ENABLED=true`, `OTEL_PYTHON_DISTRO=aws_distro`, ...). The collector config in
   [`collector/adot-collector.yaml`](../collector/adot-collector.yaml) redacts Indian identifiers before export.
4. **Know where the spans land**, and set `AGENTCORE_SPANS_LOG_GROUP` accordingly: the shared `aws/spans` log group,
   or the agent's own log group (`/aws/bedrock-agentcore/runtimes/<agent-id>-<endpoint>`, stream `spans`) depending on
   how delivery is configured.
5. **Session ids**: invoke agents with a stable `runtimeSessionId` per conversation, so the framework can group spans
   into sessions (`group_by: session`).
6. Set `filter.service_name` to the agent's OTel `service.name` (`AGENT_SERVICE_NAME`).

### Bedrock model invocation logging (`aws_bedrock_invocation_logs`)

Span content (prompts and completions) is often not captured. Invocation logs fill the gap.

- Bedrock console → *Settings* → *Model invocation logging* → enable, include text data, destination CloudWatch Logs
  (and/or S3). Or `aws bedrock put-model-invocation-logging-configuration`.
- Set `BEDROCK_INVOCATION_LOG_GROUP` (or `s3_uri` on the source).
- These logs contain prompts and outputs. Restrict who can read them, set a retention period, and keep DPDP
  tokenisation on in the framework (it tokenises on ingest).

### Optional sources

| Source | Turn on | Variable |
|---|---|---|
| `aws_agentcore_memory` (conversation history) | the agent uses AgentCore Memory | `AGENTCORE_MEMORY_ID` |
| `aws_agentcore_evaluations` (managed scores) | an AgentCore Evaluations online evaluation configuration | `AGENTCORE_EVAL_RESULTS_LOG_GROUP` |
| `aws_xray` | X-Ray tracing (or Transaction Search) | — |
| `aws_cloudwatch_metrics` | nothing (Bedrock and AgentCore publish metrics by default) | — |

## 4. Read-only permissions for online evals

| Actions | Resource |
|---|---|
| `logs:FilterLogEvents`, `logs:GetLogEvents`, `logs:StartQuery`, `logs:GetQueryResults`, `logs:DescribeLogGroups` | the span, invocation-log and evaluation-result log groups |
| `xray:GetTraceSummaries`, `xray:BatchGetTraces` | `*` |
| `cloudwatch:GetMetricData` | `*` |
| `bedrock-agentcore:ListEvents`, `bedrock-agentcore:GetEvent` | the memory ARNs (only with `aws_agentcore_memory`) |
| `s3:ListBucket`, `s3:GetObject` | only if invocation logs go to S3 |
| `bedrock:InvokeModel` | only the judge model ARNs, if judges run on Bedrock |
| `cloudwatch:PutMetricData` (namespace `GenAIEvals`) | to publish scores |

[`terraform/aws`](../terraform/README.md) creates exactly this role. No write access to the customer's agents is needed.

## 5. Schedule online evals in the customer's account

1. Build an image that contains **your** evals project (the framework image ships none):

   ```dockerfile
   FROM <framework image built from this repo's Dockerfile>
   COPY evals/ /evals/
   ```

   Push it to ECR in the customer's account.
2. Copy `terraform/aws/terraform.tfvars.example` to `terraform.tfvars`, fill it in (region, image, subnets, security
   groups, log groups, and the `environment` map with the `${VAR}` values your `sources.yaml` reads), then
   `terraform init && terraform plan && terraform apply`.
3. Check the first run in the ECS task logs, then find the scores in CloudWatch metrics, namespace `GenAIEvals`.
   The module includes an example alarm on the PII grader; add alarms for your other gates.

## 6. Verify

```bash
aws sts get-caller-identity                                        # which identity you are using
aws logs describe-log-groups --log-group-name-prefix "$AGENTCORE_SPANS_LOG_GROUP" --region "$AWS_REGION"
genai-evals validate suite.yaml --providers providers.live.yaml      # profiles resolve
genai-evals online --sources sources.yaml --suite online_checks.yaml --publish emf   # read-only dry run, writes EMF locally
```

Problems: see [troubleshooting](troubleshooting.md#aws).

References: [AgentCore observability setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html) ·
[AgentCore Evaluate API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/evaluate.html)
