# Eval pack: RAG knowledge assistant

Evaluates an assistant that answers questions from a document collection, whatever it is built on: Bedrock
Knowledge Bases, Vertex AI RAG Engine, your own vector store, or any deployed endpoint.

| | |
|---|---|
| **Measures** | retrieval (recall@4, MRR) separately from the answer (faithfulness, relevance, citations), correct refusals, privacy (structured PII in answers), safety (secrets, personal data requests, prompt injection), latency |
| **You supply** | questions with the passages that answer them ([how](data/README.md)), and one target file pointing at your system |
| **Gates** | grounded, cited, refuses-correctly, safety-cases (critical), privacy; retrieval and latency as warnings ([why](pack.yaml)) |
| **Report** | failure categories: retrieval miss, unsupported claim, missing or wrong citation, wrong refusal, off-topic answer, privacy or safety, too slow |
| **Overlay** | `insurance-policy`: motor policy questions and a no-payout-promise policy |
| **Status** | runs offline in CI; no adapter is live tested yet ([adapters](../../docs/adapters.md)) |

## Quick start

```bash
genai-evals init my-eval --pack rag-knowledge-assistant      # add --overlay insurance-policy for the insurance cases
cd my-eval                                                   # init has already run it offline: open out/offline/report.html
genai-evals run suite.yaml --allow-missing-baseline
```

## Connect your system

Pick the file in `targets/` that matches the customer's stack and set it in `.env`. Nothing else in the suite changes.

| File | Use when | Set in `.env` |
|---|---|---|
| `targets/http.yaml` | the assistant is deployed behind an API (any cloud, any framework) | `SYSTEM_URL`, `SYSTEM_TOKEN`, response field names |
| `targets/python-retriever.yaml` | you have a retriever function and want to test models on it | `RETRIEVER`, `EVAL_TARGET_PROFILE` |
| `targets/bedrock-kb.yaml` | Amazon Bedrock Knowledge Bases | `BEDROCK_KB_ID`, `AWS_REGION`, `EVAL_TARGET_PROFILE` |
| `targets/vertex-rag.yaml` | Vertex AI RAG Engine | `VERTEX_RAG_CORPUS`, `EVAL_TARGET_PROFILE` |

Then switch the judges to real models: `GENAI_EVALS_PROVIDERS=providers.live.yaml` and the profile names in
`EVAL_JUDGE_A` / `EVAL_JUDGE_B`. For retrieval graders, the system must return the ids of the passages it used.

## Connect telemetry

`sources.yaml` reads a recorded OTLP export offline. Keep the block for the customer's stack (OTLP files, AgentCore
on AWS, Cloud Trace on Google Cloud, Langfuse) and remove `fixture:`. `online_checks.yaml` grades production
sessions without reference answers.

## Files

`suite.yaml` graders, gates and report template · `schema/case.schema.json` dataset contract ·
`data/starter.jsonl` 20 synthetic cases · `targets/` how to reach the system · `providers.yaml` offline judges ·
`providers.live.yaml` live profiles · `sources.yaml` + `online_checks.yaml` monitoring · `experiment.yaml` change
comparison · `prompts/` versioned prompt for model-backed targets · `labels/` judge calibration ·
`RUNBOOK.md` engagement steps · `HANDOVER.md` what the customer owns · `validation.yaml` live / customer validation
records · `sim_system.py` the offline simulator.
