# Configuration reference

## Principles

- **The framework ships no settings.** Accounts, regions, projects, model ids, ARNs, log groups and keys belong to
  the partner or customer. They live in your own evals project and your own environment, never in this repo.
- **YAML is shareable, values are not.** Commit `suite.yaml`, `providers.live.yaml`, `sources.yaml`, datasets and
  prompts. Put every environment-specific value in an environment variable and reference it as `${VAR}`.
- **Secrets never go in YAML.** Inline `api_key`, `secret`, `password` or `token` values are rejected. Use
  `auth.api_key_env` (the name of an environment variable) or `auth.api_key_secret` (a secret manager reference).
  For AWS and Google Cloud, use their standard credential chains: no keys at all.

## Files

| File | Purpose | Loaded by |
|---|---|---|
| `providers.yaml` / `providers.live.yaml` | provider profiles (targets, judges, simulator, embeddings) | `--providers`, `$GENAI_EVALS_PROVIDERS`, or `providers.yaml` next to the suite |
| `suite.yaml` | dataset, targets, graders, gates | `genai-evals run / validate` |
| `sources.yaml` | production telemetry connectors | `genai-evals online --sources` |
| `online_checks.yaml` | a suite without targets or dataset, for online evals | `genai-evals online --suite` |
| `prompts/<id>/<version>/` + `prompts.lock` | versioned prompts and stages | suites (`prompts_dir`), `genai-evals prompts` |
| `.env` | your values (never commit) | loaded automatically from the working directory, or `--env-file` |
| `targets/*.yaml` | the system under test, one file per way of reaching it | `targets_from:` in a suite (pack projects use `EVAL_TARGETS`) |
| `schema/case.schema.json` | the dataset contract | `dataset_schema:` in a suite |
| `overlays/<name>/overlay.yaml` | extra cases, graders, gates and failure categories | `overlays:` in a suite |
| `validation.yaml` | live tested / customer validated records for adapters | `validation:` in a suite, or `validation.yaml` next to it |
| `experiment.yaml` | baseline and candidate variants | `genai-evals experiment run` |

`genai-evals init <dir> --pack <pack>` writes all of these with working defaults for one workflow ([packs](packs.md)).

## Environment variables

### Interpolation syntax

Used in any string value of `providers*.yaml`, `suite.yaml`, `sources.yaml` and `online_checks.yaml`:

| Syntax | Meaning |
|---|---|
| `${VAR}` | required: loading fails and names every unset variable |
| `${VAR:-default}` | `default` when `VAR` is unset or empty |
| `${VAR-default}` | `default` only when `VAR` is unset |
| `$${VAR}` | a literal `${VAR}` |

Quote values that contain `${...}` inside inline mappings or lists: `{region: "${AWS_REGION}"}`.
Interpolation is not applied to prompt files (their content is hashed) or datasets.

### `.env` files

`genai-evals` reads `./.env` if it exists, or the file given with `--env-file`. Lines are `KEY=VALUE` (optionally
`export KEY=VALUE`, quoted values, `#` comments). **Variables already set in the environment win**, so CI and
container platforms can inject the same names from their own secret stores.

### Variables the framework itself reads

| Variable | Used for |
|---|---|
| `GENAI_EVALS_PROVIDERS` | default providers file when `--providers` is not given |
| `GENAI_EVALS_TOKEN_KEY` | key for DPDP tokenisation; set a stable value (from a secret manager) so tokens match across runs |
| `GENAI_EVALS_PROMPTS_DIR` | prompts directory for `genai_evals.prompts.load()` in application code (default `prompts`) |
| `GENAI_EVALS_PROMPT_STAGE` | stage for `load()` (default `prod`) |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Langfuse source and prompt mirror |
| `GITHUB_SHA` | recorded on results when present |
| Provider SDK variables | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_PROFILE` / `AWS_REGION`, `GOOGLE_APPLICATION_CREDENTIALS` etc. follow each SDK's own rules |

Everything else (`AWS_REGION`, `GCP_PROJECT`, `BEDROCK_MODEL_ID`, ...) is just a name used by your YAML. The
`.env.example` that `genai-evals init` writes lists the conventional names.

## `providers.yaml`

```yaml
profiles:
  <name>:
    provider: openai | anthropic | bedrock | vertex | vertex_anthropic | openai_compatible | mock
    model: "${MODEL_VAR}"            # model id, snapshot or Bedrock inference profile ARN
    region: "${AWS_REGION}"          # bedrock
    project: "${GCP_PROJECT}"        # vertex, vertex_anthropic
    location: "${GCP_LOCATION}"      # vertex, vertex_anthropic
    base_url: "${GATEWAY_BASE_URL}"  # openai_compatible
    auth: {...}                      # see below
    options: {...}                   # provider-specific, see below
    pricing: {input_per_mtok: 0, output_per_mtok: 0, cached_input_per_mtok: 0}   # USD per million tokens
    max_concurrency: 8               # parallel calls for this profile
    rpm: 600                         # requests per minute cap (optional)
    retries: 3                       # transient errors only (throttling, timeouts, 5xx)
    timeout_s: 120
    family: anthropic                # override the model family used by the judge-family policy
```

| `auth` key | Meaning |
|---|---|
| `api_key_env: NAME` | read the key from environment variable `NAME` |
| `api_key_secret: aws-sm://<secret-id>[#field]` | AWS Secrets Manager (optional JSON field) |
| `api_key_secret: gcp-sm://projects/<p>/secrets/<s>[/versions/<v>]` | Google Secret Manager |
| `api_key_secret: env://NAME` | same as `api_key_env` |
| `aws_profile: NAME` / `role_arn: ARN` | Bedrock: named profile or role to assume (default: standard AWS chain) |
| *(none)* | OpenAI / Anthropic fall back to `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`; Vertex uses ADC |

| `options` | Provider | Meaning |
|---|---|---|
| `store: false` | openai | do not store responses (default; `check-config` fails on `true`) |
| any Responses API parameter | openai | passed through, e.g. `reasoning: {effort: low}` |
| any `GenerateContentConfig` field | vertex | passed through, e.g. `thinking_config`, `safety_settings` |
| `guardrail_id`, `guardrail_version` | bedrock | apply a Bedrock guardrail and record its trace |
| `embedding_model` | openai, bedrock, vertex | model used when the profile serves `embeddings:` |
| `mode`, `responses`, `default`, `strictness` | mock | offline behaviour (`echo`, `scripted`, `judge`) |

`genai-evals check-config <providers.yaml>` runs production guardrails (no stored responses, pricing present).

## `suite.yaml`

| Key | Default | Meaning |
|---|---|---|
| `name`, `description` | | identify the suite in reports and baselines |
| `dataset` | | path to JSONL / JSON / CSV / YAML, or an inline list of cases |
| `prompts_dir` | | directory with `prompts/<id>/<version>/` (relative to the suite) |
| `targets` | | systems under test (below) |
| `graders` | | list of graders (below) |
| `gates` | | release gates (below) |
| `judges` | | profile names for model graders. **Required** when any model grader is used; there is no default |
| `judge_policy` | `{}` | `require_different_family: true`, `aggregate: mean`, `min_agreement` |
| `simulator` | | profile that plays the user for cases with `goal` / `persona` |
| `embeddings` | | profile for `embedding_similarity` |
| `repeats` | 1 | runs per case (use 3–5 for agents to measure `pass^k`) |
| `concurrency` | 8 | parallel cases |
| `seed` | 7 | resampling seed (bootstrap) |
| `targets_from` | | YAML file with a `targets:` list, added to `targets` (packs: `"${EVAL_TARGETS:-targets/offline.yaml}"`) |
| `dataset_schema` | | JSON Schema every case must satisfy; a bad row stops the run naming the case and field |
| `extra_datasets` | `[]` | more case files, loaded after `dataset` |
| `overlays` | `[]` | overlay folders to merge ([packs](packs.md#overlays)) |
| `report` | | report template: `title`, `intro`, `headline` (grader), `failure_categories` (category → graders) |
| `validation` | `validation.yaml` next to the suite | adapter validation records shown in reports |
| `pack` | | the pack and version the suite came from (informational, recorded in results) |
| `calibration`, `min_judge_kappa` | | calibration file from `genai-evals calibrate`, and the agreement (lower bound of the κ 95% CI) a judge must reach before its gates count |
| `min_calibration_n` | 30 | human labels a judge needs before it can be trusted |
| `min_n` | 5 | scored cases a gate needs (overall, per slice and in the baseline overlap); fewer makes the gate `invalid` |
| `on_error` | `fail` | `fail`: target and grader errors count as failed scores (value 0). `exclude`: left out of means (diagnosis only) |
| `tokenize`, `tokenizer` | `true`, `{}` | DPDP tokenisation of inputs and outputs before judges and storage ([DPDP](dpdp.md)) |

### Targets

| Key | Meaning |
|---|---|
| `name` | unique within the suite |
| `kind` | `model`, `prompt`, `rag`, `agent`, `http`, `python` |
| `profile` | provider profile (model, prompt, rag, agent with `tool_loop`) |
| `prompt` | `id` (uses `prompt_stage`, default `prod`) or `id@1.2.0` |
| `system`, `template` | inline prompt when not using `prompts/` |
| `framework` | agents: `tool_loop`, `openai_agents_sdk`, `agentcore_runtime`, `bedrock_agent`, `agent_engine` |
| `tools_module` | Python module with tool implementations for `tool_loop` |
| `callable` | `module:function` for `python` targets (and the Agents SDK agent factory) |
| `retriever` | `rag`: `{type: python | bedrock_kb | vertex_rag, ...}` |
| `max_steps` | agent step limit (default 12) |
| `output` | `text` or `json` |
| `options` | adapter settings, e.g. `agent_arn`, `region`, `resource`, `url`, `headers`, `body`, `map` |

Per-adapter options and examples: [providers and targets](providers.md).

### Graders and gates

```yaml
graders:
  - {name: correctness, type: reference_match, threshold: 0.8, tags: [billing], judges: [judge-a, judge-b]}
gates:
  - {name: correct, rule: "mean(correctness) >= 0.8", severity: block, targets: [candidate]}
```

Grader keys: `name`, `type`, `weight`, `threshold` (value ≥ threshold passes), `tags` (grade only cases with these
tags), `judges` (override), plus type-specific parameters ([graders](graders.md)). Gate keys: `name`, `rule`,
`severity` (`block` or `warn`), `targets`, `min_n` (overrides the suite's), `allow_missing_baseline` (a `delta()` gate
passes with "no baseline" instead of being `invalid`). Rule syntax and outcomes: [gates](gates.md).

### Cases (dataset rows)

| Key | Meaning |
|---|---|
| `id` | unique |
| `input` | text, a list of `{role, content}` messages, or a dict of template variables |
| `reference` | expected values used by graders: `answer`, `answerable`, `fields`, `gold_passages`, `context`, `tool_calls`, `tool_sequence`, `final_state`, `handoff`, `label`, `should_refuse`, `guardrail` |
| `tags` | list of slice labels |
| `critical` | must pass for `critical_pass()` |
| `turns` / `goal`, `persona`, `max_turns` | multi-turn: scripted user turns, or a goal card for the simulator |
| `source`, `lineage_ref` | `synthetic`, `production` or `sme`; DPDP lineage id for production-derived rows |
| `metadata` | free-form |

## Experiment files

An experiment file names a suite, the target to vary, a baseline and variants (`prompt`, `profile`, `options`,
`retriever`, `overrides`), plus `repeats`, `seed`, `alpha` and `correction`. Values can use `${VAR}` like any other
file. Full reference: [experiments](experiments.md#the-experiment-file).

## Maturity of each integration

Each adapter is *implemented*, *fixture tested* (passes the conformance kit in CI), *live tested* or *customer
validated* (the last two recorded per project in `validation.yaml`); preview APIs are also flagged *experimental*.
The current catalog with conformance results is in [adapters](adapters.md); the definitions and how to record a
validation are in [plugins](plugins.md#maturity-fixture-tested-live-tested-customer-validated).

## `sources.yaml`

```yaml
sources:
  - name: <label>              # shown in reports
    type: <connector>          # see sources.md
    window: {last: 24h}        # or {start: <ISO time>, end: <ISO time>}
    sampling: 0.05             # fraction of sessions to grade; a session is in or out by a hash of its id (seed: 7)
    filter: {...}              # connector-specific, e.g. service_name, agent_arn, session_id
    join: [ {type: <connector>, ...}, ... ]    # enrich with prompts, history, managed scores, metrics
    fixture: <path>            # read recorded records instead of calling the API (tests, demos)
    # connector options: region, log_group, project, location, table, columns, resource, memory_id, actor_id,
    # dimensions, metrics, group_by, max_traces, max_records, path, s3_uri, trace_filter, log_filter
```

Connector list and what each returns: [sources](sources.md). Cloud prerequisites: [AWS](setup-aws.md),
[Google Cloud](setup-gcp.md).

## Results store and outputs

| Flag | Meaning |
|---|---|
| `--out <dir>` | output folder (default `.genai-evals/runs/<run_id>`): `result.json`, `summary.md`, `junit.xml`, `report.html`, `scorecard.html` |
| `--store <url>` | results store: `file://<folder>`, `sqlite:///...` or `postgresql+psycopg://...` ([results](results.md#stores)) |
| `--publish emf,cloudwatch,cloud_monitoring,otlp` | publish scores as metrics (`--region` / `--project`) |
| `--otel console|otlp|<url>` | emit eval spans and metrics |
| `--summary-md <path>` | write the PR-comment Markdown |
| `--env-file <path>` | load settings from this file instead of `./.env` |
