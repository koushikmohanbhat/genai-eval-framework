# Architecture

```mermaid
flowchart LR
  subgraph SPEC["In the app repo"]
    Y["suite.yaml"] --> D[("dataset (tokenised)")]
    PR["prompts/ + prompts.lock"]
    PRV["providers.yaml"]
    SRC["sources.yaml"]
  end
  subgraph RUN["Offline runner"]
    P["plan: cases × targets × repeats"] --> EX["execute (async, rate limits, retries, cache)"] --> AD["target adapters"]
  end
  subgraph ING["Online ingestion"]
    AWS["CloudWatch aws/spans · X-Ray · Bedrock invocation logs · AgentCore Memory / Evaluations"]
    GCP["Cloud Trace · Cloud Logging · BigQuery request-response · Agent Engine Sessions"]
    OTH["OpenAI Agents SDK traces · OTLP · Langfuse · usage APIs"]
    AWS & GCP & OTH --> N["normaliser → Sample (OTel GenAI conventions) + joins"]
  end
  subgraph G["Grading"]
    DG["deterministic"] --> MG["judge panel (provider profiles)"] --> HQ["human labels → calibration"]
  end
  subgraph OUT["Results"]
    ST[("store: SQLite / Postgres")] --> STAT["stats: CIs, paired deltas, pass^k, slices"] --> GT["gates → exit code"]
    STAT --> REP["HTML report · scorecard · Markdown · JUnit"]
    STAT --> PUB["CloudWatch · Cloud Monitoring · OTLP"]
  end
  Y --> P
  PRV --> AD & MG
  PR --> AD
  SRC --> ING
  AD --> G
  N --> G
  G --> ST
```

## The one shape: `Sample`

Every target adapter and every telemetry source produces the same `Sample`: input, output (text and parsed JSON),
messages, retrieved `context`, tool-call `trajectory` (tools, handoffs, retrievals), `final_state`, token usage, cost,
latency / TTFT, steps, trace and session ids, and `prompt_versions`. Because of this, a grader written once runs offline on
fresh runs and online on production traffic, on any cloud.

## Run flow

1. **Validate.** The schema is checked, every profile referenced must exist, model graders need explicitly named judges,
   gate rules are parsed and their names resolved, and prompts are checked against `prompts.lock`.
2. **Execute.** Targets run concurrently. Calls go through provider profiles, each with its own concurrency cap, RPM
   spacing and retries on transient errors. Pure targets (prompt / model / RAG) are cached by a content hash of the case,
   the target configuration and the prompt hash, so unchanged cases are not re-run.
3. **Grade.** Deterministic graders run on the raw sample. Model graders send **tokenised** text to the judge panel.
4. **Aggregate.** Per-case means over repeats, bootstrap CIs over cases, pass@k / pass^k, slices by tag, critical cases,
   ops percentiles, and paired deltas against a baseline.
5. **Gate.** Rules are evaluated per target. A blocking failure makes the exit code 1. A gate whose grader's judge fails
   calibration is marked *invalid*.
6. **Store and report.** Samples are tokenised before they are written anywhere. Outputs are HTML, scorecard, Markdown,
   JUnit and JSON, with optional SQL storage, metric publishing and OTel spans.

## Package layout

| Module | Responsibility |
|---|---|
| `config` | Pydantic schemas for providers, suites, sources; static validation |
| `providers/` | OpenAI Responses, OpenAI-compatible, Anthropic (direct / Vertex), Bedrock Converse, Vertex Gemini, mock |
| `targets/` | model / prompt, RAG, HTTP, Python, agents (tool loop, Agents SDK, AgentCore Runtime, Bedrock Agents, Agent Engine), user simulator |
| `graders/` | registry and graders; judge panel |
| `stats`, `gates`, `calibration` | aggregation, DSL, judge agreement |
| `prompts` | prompts as code, runtime `load()`, registry sync |
| `sources/` | connectors, OTel normaliser, joins |
| `online` | production grading pipeline |
| `report/`, `publish`, `otel` | outputs |
| `interop/` | Promptfoo, AgentCore Evaluations, Vertex Gen AI evaluation |
| `dpdp` | tokenisers and DPDP framework integration |
