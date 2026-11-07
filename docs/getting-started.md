# Getting started (for partners)

This guide takes you from nothing to a gated, monitored evaluation that the customer owns. The repeatable unit is an
**eval pack**: a complete project for one workflow that runs offline on day 0 and is pointed at the customer's system
by changing configuration, not code. The framework repo holds **no settings**: no accounts, regions, projects,
model ids or keys.

| Step | Time | Needs |
|---|---|---|
| [1. Install](#1-install) | 2 min | Python 3.10+ |
| [2. Scaffold a project from a pack](#2-scaffold-a-project-from-a-pack) | 2 min | nothing |
| [3. Agree what good means](#3-agree-what-good-means) | a workshop | the customer's product owner and SMEs |
| [4. Bring the customer's cases](#4-bring-the-customers-cases) | days | 100–300 cases |
| [5. Connect the system and judges](#5-connect-the-system-and-judges) | 30 min | credentials for one provider |
| [6. Gate releases](#6-gate-releases) | 1 hour | CI with access to the providers |
| [7. Monitor production](#7-monitor-production) | half a day | read access to the customer's telemetry |
| [8. Hand over](#8-hand-over) | a session | the team that will own it |

Every project gets a `RUNBOOK.md` with the same steps and its pack's specifics, and `genai-evals doctor` tells you
which are done ([engagement guide](engagement.md)).

## 1. Install

The package is not on PyPI yet, so install it from a GitHub release tag ([releases](https://github.com/koushikmohanbhat/genai-eval-framework/releases)). Pin the tag so a change on `main` never reaches your customers unannounced.

```bash
python -m venv .venv && source .venv/bin/activate
pip install "genai-eval-framework[openai,aws,gcp,anthropic] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@v0.3.0"
genai-evals --version
```

Install only the extras you need:

| Extra | Adds | Needed for |
|---|---|---|
| `openai` | `openai` | OpenAI targets and judges, OpenAI usage source |
| `agents` | `openai-agents` | OpenAI Agents SDK targets and trace processor |
| `anthropic` | `anthropic` | Anthropic direct, Claude on Vertex |
| `aws` | `boto3` | Bedrock, AgentCore, Bedrock Agents / KB, CloudWatch, X-Ray, Secrets Manager |
| `gcp` | Google Cloud SDKs | Vertex AI, Agent Engine, Cloud Trace, Cloud Logging, BigQuery, Cloud Monitoring, Secret Manager |
| `otel` | OpenTelemetry SDK + OTLP exporter | `--otel` (eval spans and metrics) |
| `langfuse` | `langfuse` | Langfuse source and prompt mirror |
| `postgres` | `psycopg` | Postgres results store |

No extra is needed to run a pack offline.

## 2. Scaffold a project from a pack

Pick the pack for the customer's workflow:

| Pack | Evaluates |
|---|---|
| `rag-knowledge-assistant` | questions answered from documents: retrieval, grounded answers, citations, refusals, privacy and prompt-injection safety |
| `tool-agent` | an agent acting on business systems through tools: outcomes, tool use, policy edges, escalation, reliability across repeats |
| `structured-extraction` | documents to schema-valid JSON: field accuracy, totals, rejection of the wrong document type |

```bash
genai-evals pack list
genai-evals init evals --pack rag-knowledge-assistant            # add --overlay insurance-policy for industry cases
```

`init` writes a complete project and runs it offline on a simulated system, so the first thing you have is a sample
report (`evals/out/offline/report.html`) and a monitoring demo on recorded telemetry (`evals/out/online/`). Use them
in the first customer meeting. The project contains:

```
evals/
  suite.yaml                 # graders, gates, report template; the system comes from targets/
  schema/case.schema.json    # the dataset contract (rows are validated on load)
  data/starter.jsonl         # synthetic starter cases;  data/README.md: how to add customer cases
  targets/                   # one file per way of reaching the system (offline simulator, HTTP, Bedrock, Vertex, ...)
  providers.yaml             # offline judges;  providers.live.yaml: live profiles, every value from ${VAR}
  sources.yaml               # telemetry for monitoring (recorded fixture until you connect a source)
  online_checks.yaml         # reference-free production checks
  experiment.yaml            # compare candidate changes side by side
  prompts/ + prompts.lock    # versioned prompts for model-backed targets
  RUNBOOK.md  HANDOVER.md    # the engagement steps; what the customer owns afterwards
  validation.yaml            # live / customer validation records for adapters
  .env.example  ci/  baselines/
```

Commit everything except `.env` and `out/`.

## 3. Agree what good means

Walk the customer through the sample report. The pack proposes failure categories, critical cases and gates
(`genai-evals pack info <pack>` lists them with the reason for each); agree what to keep, what to change and who
owns each area, and write it into `suite.yaml` and `HANDOVER.md`.

## 4. Bring the customer's cases

Follow `data/README.md` in the project. Rows are checked against `schema/case.schema.json` every time the suite
loads, and a bad row stops the run naming the case and the field:

```bash
genai-evals validate suite.yaml
```

Personal data in cases must be tokenised first ([DPDP](dpdp.md)). Industry-specific cases belong in an overlay
(a folder with `overlay.yaml`), not in the base dataset ([packs](packs.md#overlays)).

## 5. Connect the system and judges

Choose the file in `targets/` that matches the customer's stack and set it in `.env`; the suite doesn't change:

```bash
cp .env.example .env
# .env
EVAL_TARGETS=targets/http.yaml            # or targets/bedrock-kb.yaml, targets/agentcore.yaml, ...
SYSTEM_URL=https://...                    # whatever that target file reads
GENAI_EVALS_PROVIDERS=providers.live.yaml
EVAL_JUDGE_A=judge-openai
EVAL_JUDGE_B=judge-gemini
```

```bash
genai-evals check-config providers.live.yaml
genai-evals run suite.yaml --out out/live-1 --allow-missing-baseline
```

Cloud-specific setup: [AWS](setup-aws.md) · [Google Cloud](setup-gcp.md) · [OpenAI and Anthropic](setup-openai-anthropic.md).
Then calibrate the judges against SME labels ([calibration](graders.md#calibration)); judge-graded gates stay
*invalid* until the judges are trusted.

The report's **Adapters used** table shows how far each integration has been proven. When an adapter works against
the customer's live service, record it:

```bash
genai-evals adapters record --kind provider --name bedrock --status live_tested --environment "customer sandbox" --evidence out/live-1/result.json
```

## 6. Gate releases

```bash
genai-evals run suite.yaml --out out/main --allow-missing-baseline
genai-evals baseline promote out/main/result.json --out baselines/main.json
```

Copy `ci/github-actions-evals.yml` into the customer's repository, or port two commands to their CI: the exit code is
the gate (0 pass, 1 blocking gate failed or invalid, 2 configuration error). See [CI](ci.md) and [gates](gates.md).
For changes with several candidates (a model upgrade, prompt variants) use [experiments](experiments.md).

## 7. Monitor production

Keep the customer's source in `sources.yaml` (OTLP files, AgentCore on AWS, Cloud Trace on Google Cloud, Langfuse),
remove `fixture:` and run:

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --out out/online --publish cloudwatch
```

Online scores are monitoring signals, not release evidence ([what they mean](sources.md#what-online-scores-mean)).

## 8. Hand over

Fill in `HANDOVER.md` with the customer: owners for the dataset, baselines, judges, secrets, CI and monitoring, and
every checklist item ticked. Then:

```bash
genai-evals doctor
```

It passes when the project meets the [definition of repeatable](engagement.md#definition-of-repeatable): offline
demo, one live provider and one live telemetry source, an understandable report, a meaningful release gate, and a
handover with a maintenance path.

## Where to go next

[Packs](packs.md) · [Engagement guide](engagement.md) · [Configuration reference](configuration.md) ·
[Adapters and their status](adapters.md) · [Writing an adapter](plugins.md) · [Results format](results.md) ·
[Experiments](experiments.md) · [Troubleshooting](troubleshooting.md)
