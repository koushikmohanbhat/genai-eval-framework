# Getting started (for partners)

This guide takes you from nothing to a gated eval running against your customer's models in their cloud. The
framework repo holds **no settings**: no accounts, regions, projects, model ids or keys. You keep your evals project
(suites, datasets, prompts) in your own or your customer's repo, and supply every environment-specific value
through environment variables.

| Step | Time | Needs |
|---|---|---|
| [1. Install](#1-install) | 2 min | Python 3.10+ |
| [2. Run the demo](#2-run-the-offline-demo) | 1 min | nothing |
| [3. Create your evals project](#3-create-your-evals-project) | 5 min | nothing |
| [4. Connect real models](#4-connect-real-models) | 30 min | credentials for at least one provider |
| [5. Bring your use case](#5-bring-your-use-case) | days | customer SMEs, 50–300 cases |
| [6. Gate releases in CI](#6-gate-releases-in-ci) | 1 hour | CI with access to the providers |
| [7. Grade production traffic](#7-grade-production-traffic-online-evals) | half a day | read access to the customer's telemetry |

## 1. Install

The package is not on PyPI yet, so install it from a GitHub release tag ([releases](https://github.com/koushikmohanbhat/genai-eval-framework/releases)). Pin the tag so a change on `main` never reaches your customers unannounced.

```bash
python -m venv .venv && source .venv/bin/activate
pip install "genai-eval-framework[openai,aws,gcp,anthropic] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@v0.1.0"
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

To work on the framework itself: `git clone` the repo and `pip install -e ".[dev]"`.

## 2. Run the offline demo

```bash
git clone https://github.com/koushikmohanbhat/genai-eval-framework && cd genai-eval-framework
pip install -e ".[dev]"
python examples/insurance/demo.py
```

It runs in a few seconds with no keys and writes HTML reports to `genai-evals-demo/`. Use it to show a customer
what a gate, a judge panel, a prompt promotion and an online eval look like before touching their systems.

## 3. Create your evals project

In the application repo (yours or the customer's):

```bash
genai-evals init evals
cd evals
genai-evals run suite.yaml        # offline mocks: passes every gate, writes .genai-evals/runs/<id>/report.html
```

`init` writes a small, working project:

```
evals/
  suite.yaml              # dataset, targets, graders, gates
  providers.yaml          # offline mock profiles (no keys)
  providers.live.yaml     # real profiles: every environment-specific value is ${VAR}
  sources.yaml            # production telemetry for online evals
  online_checks.yaml      # reference-free graders and gates for online evals
  data/cases.jsonl        # golden set
  prompts/ + prompts.lock # versioned prompts
  .env.example            # every variable the YAML reads, with no values
  .gitignore              # ignores .env and local results
  ci/github-actions-evals.yml
```

Commit everything except `.env`.

## 4. Connect real models

1. Set up credentials and permissions for the providers you will use:
   [AWS](setup-aws.md) · [Google Cloud](setup-gcp.md) · [OpenAI and Anthropic](setup-openai-anthropic.md).
2. In `providers.live.yaml`, delete the profiles you don't use.
3. `cp .env.example .env` and fill in only the variables your remaining profiles reference. For example, to test a
   Bedrock model judged by OpenAI and Gemini:

   ```bash
   GENAI_EVALS_PROVIDERS=providers.live.yaml
   EVAL_TARGET_PROFILE=bedrock
   EVAL_JUDGE_A=judge-openai
   EVAL_JUDGE_B=judge-gemini
   AWS_REGION=<region>
   BEDROCK_MODEL_ID=<model id or inference profile ARN>
   OPENAI_API_KEY=<key>            # or use auth.api_key_secret with a secret manager
   JUDGE_OPENAI_MODEL=<model>
   GCP_PROJECT=<project>
   GCP_LOCATION=<location>
   JUDGE_VERTEX_MODEL=<model>
   ```

4. Check, then run:

   ```bash
   genai-evals check-config providers.live.yaml     # fails on store: true; warns when pricing is missing
   genai-evals validate suite.yaml                   # profiles, dataset, prompts, graders and gates
   genai-evals run suite.yaml
   ```

`genai-evals` loads `./.env` automatically (or `--env-file <path>`). Variables already set in the shell win, so CI
can inject the same names from its own secret store. Every option is described in [configuration](configuration.md).

To compare providers on the same cases, add more targets that differ only in `profile` (see
[providers](providers.md#comparing-providers-on-the-same-cases)).

## 5. Bring your use case

1. **Define "good" with the customer's SMEs**: which answers are correct, what must never happen (critical cases),
   and the business threshold for each metric.
2. **Build the golden set** in `data/cases.jsonl`, one case per line:

   ```json
   {"id": "claim-017", "input": "...", "tags": ["hinglish", "motor"], "critical": true,
    "reference": {"answer": "...", "answerable": true}}
   ```

   Use tags for every slice you will report on (language, product, intent, risk). Tokenise or synthesise anything
   that contains personal data ([DPDP](dpdp.md)). For agents add `reference.tool_calls` / `final_state`; for RAG add
   `reference.gold_passages`. Aim for 50–300 cases; the report warns when the set is too small to detect a
   difference.
3. **Point the target at the real system**: a prompt in `prompts/`, a RAG retriever, a provider-neutral tool loop, or
   a deployed agent (AgentCore Runtime, Bedrock Agents, Vertex Agent Engine, an HTTP endpoint). See
   [providers and targets](providers.md).
4. **Choose graders** ([graders](graders.md)): deterministic checks first, then a judge panel for what they can't
   decide. Keep two judges from different model families than the target (`judge_policy: {require_different_family: true}`).
5. **Calibrate the judges** against 30–50 human labels, and require agreement before trusting their gates:
   `genai-evals calibrate <result.json> --grader <name> --labels labels.csv`, then set `calibration:` and
   `min_judge_kappa:` in the suite.
6. **Set gates** ([gates](gates.md)) with the business owner, and mark the cases that must never fail as `critical`.

## 6. Gate releases in CI

```bash
genai-evals run suite.yaml --out out/main
genai-evals baseline promote out/main/result.json --name main --out baselines/main.json   # commit it
```

Copy `ci/github-actions-evals.yml` to `.github/workflows/evals.yml`, put the variable values in GitHub repository
variables and secrets, and uncomment the `baseline:` line. Pull requests then get a comment with gate results,
deltas with confidence intervals and the cases that changed. CodeBuild and Cloud Build examples are in [CI](ci.md).
Prompt changes are gated the same way ([prompts](prompts.md)).

## 7. Grade production traffic (online evals)

1. Make sure the customer's agents emit the telemetry the sources need
   ([AWS](setup-aws.md#3-turn-on-the-telemetry-online-evals-read), [Google Cloud](setup-gcp.md#3-turn-on-the-telemetry-online-evals-read)).
2. Keep the source for their cloud in `sources.yaml`, set its variables, and write reference-free checks in
   `online_checks.yaml` (privacy, policy, latency, faithfulness when context is captured).
3. Try it from a laptop with read-only credentials:

   ```bash
   genai-evals online --sources sources.yaml --suite online_checks.yaml --publish emf
   ```

4. Schedule it inside the customer's account or project with the Terraform in [`terraform/`](../terraform/README.md),
   and alarm on the published scores next to their existing alarms.

## Where to go next

[Configuration reference](configuration.md) · [Troubleshooting](troubleshooting.md) ·
[Partner packaging](partner-playbook.md) · [Limitations](limitations.md)
