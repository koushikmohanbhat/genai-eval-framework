# Running an engagement

The framework is built so the next engagement takes less custom work than the last one, without dictating the
customer's architecture. This page is the partner's view: what a repeatable engagement is, how to check you are
there, and what the customer owns when you leave.

## Definition of repeatable

A new partner takes a clean project from a pack and, changing only configuration and customer data, can:

1. **run the offline demo**;
2. **connect one supported provider and one telemetry source**;
3. **produce an understandable report**;
4. **set a meaningful release gate**;
5. **hand the project to the customer with a clear maintenance path**.

That is the completion bar for the framework, ahead of adding more integrations. `genai-evals doctor` checks it for
a project:

```text
$ genai-evals doctor
Engagement readiness for acme-kb-eval: 5/8
  [todo] handover: 6 unticked item(s), 2 owner(s) not named
  [todo] adapters validated: not yet recorded as live tested: provider bedrockline demo: runs offline (20 cases, all blocking gates pass)
  [todo] handover: 6 unticked item(s), 2 owner(s) not named
  [todo] adapters validated: not yet recorded as live tested: provider bedrockvider connected: bedrock in out/live-1/result.json
  [todo] telemetry connected: no online run on live telemetry yet (fixture runs don't count)
         next: keep your source in sources.yaml, remove `fixture:`, `genai-evals online ... --out out/online`
  [todo] handover: 6 unticked item(s), 2 owner(s) not named
  [todo] adapters validated: not yet recorded as live tested: provider bedrockort: engineer report and scorecard with failure categories
  [todo] release gate: no CI workflow running genai-evals
         next: RUNBOOK step 6
  [todo] handover: 6 unticked item(s), 2 owner(s) not named
  [todo] adapters validated: not yet recorded as live tested: provider bedrockges calibrated: all judges trusted
  [todo] handover: 6 unticked item(s), 2 owner(s) not named
  [todo] adapters validated: not yet recorded as live tested: provider bedrock
```

| Doctor check | Passes when |
|---|---|
| offline demo | the suite runs on the simulator and mock judges |
| provider connected | a result exists from a run with a live (non-mock) provider |
| telemetry connected | an online result exists that read live telemetry (not a fixture) |
| report | that live run has an engineer report and scorecard, and the suite has a report template with failure categories |
| release gate | a blocking gate on a quality grader, a baseline in `baselines/`, and a CI workflow that runs genai-evals |
| judges calibrated | every judge-graded grader has a trusted calibration (or the pack has no judges) |
| handover | `HANDOVER.md` has no unticked items and every owner is named |
| adapters validated | every adapter the live runs used is recorded as live tested or better in `validation.yaml` |

## The engagement, step by step

The project's `RUNBOOK.md` has the commands for its pack. In outline:

| Step | Partner does | Evidence |
|---|---|---|
| Day 0 | `genai-evals init --pack ...`, show the sample report | `out/offline/` |
| Workshop | agree failure categories, critical cases, gate thresholds, owners | `suite.yaml`, `HANDOVER.md` owners |
| Dataset | SMEs write cases; production cases tokenised; schema-validated | `data/`, `genai-evals validate` |
| Connect | pick a target file, live providers, `adapters record` | `out/live-*/`, `validation.yaml` |
| Calibrate | 30+ SME labels per judge-graded grader | `calibration.json` |
| Gate | baseline, CI workflow, a deliberate regression fails the PR | `baselines/main.json`, CI run |
| Monitor | one live source, scheduled online run, alerts | `out/online/`, alarms |
| Change | candidate prompts / models as experiments | `experiments/` comparison and manifest |
| Hand over | walk through `HANDOVER.md`; the customer runs it themselves | `genai-evals doctor` passes |

## What the customer owns afterwards

Every pack's `HANDOVER.md` covers, with an owner per area:

- **Dataset updates**: adding cases, reviewing production failures, keeping tokenisation and lineage.
- **Baseline changes**: when to promote a new baseline, and who approves it.
- **Judge calibration**: labels, re-labelling after judge or rubric changes.
- **Secrets and access**: keys in the customer's secret manager, the token key, read-only cloud roles.
- **CI gate thresholds**: the agreed thresholds and the reasons, critical cases, exit codes.
- **Online monitoring**: the schedule, sampling rate, alerts and who receives them.
- **Adapters and validation**: live / customer validation records for everything in use.
- **Pack-specific items**: e.g. the document index for RAG, the tool sandbox for agents, the output schema for extraction.

## Keeping the customer's architecture intact

- The system under test is reached through a target file (HTTP, a cloud's managed service, or a Python function),
  so the customer's stack doesn't change for the evaluation.
- Telemetry is read from what the customer already runs (OTLP files, AgentCore, Cloud Trace, Langfuse), read-only,
  in their account.
- Results are plain JSON with exports to CSV / JSONL and Promptfoo, stored in a folder, SQLite or their Postgres.
- Anything missing is an [adapter plugin](plugins.md) in the partner's own package, proven with the conformance kit,
  not a fork of the framework.
