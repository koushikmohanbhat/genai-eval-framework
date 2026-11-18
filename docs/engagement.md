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
$ genai-evals doctor acme-kb-eval
Engagement readiness for acme-kb-eval: 4/9
  [pass] offline demo: runs offline (23 cases, all blocking gates pass)
  [pass] provider connected: bedrock in out/live-1/result.json
  [todo] telemetry connected: no online run on live telemetry yet (fixture runs don't count)
         next: keep your source in sources.yaml, remove `fixture:`, `genai-evals online ... --out out/online`
  [pass] report: engineer report and scorecard with failure categories
  [todo] release gate: no CI workflow running genai-evals
         next: RUNBOOK step 6
  [todo] eval set: 15 representative + 8 stress cases; representative set too small (15 < 30): sample it from production, since its score is the production estimate; stress suite has no cases for: structured_input, image_input, typos, very_short
         next: genai-evals cases coverage suite.yaml (docs/growing-the-eval-set.md)
  [pass] judges calibrated: all judges trusted (on the calibration sample only; no representative validation sample for: context_precision, context_recall, faithfulness, relevance, safe_content)
  [todo] handover: 19 unticked item(s), 6 owner(s) not named
         next: walk through HANDOVER.md with the customer
  [todo] adapters validated: not yet recorded as live tested: provider bedrock
         next: genai-evals adapters record --kind <kind> --name <name> --status live_tested ...
```

This is a real transcript, from a `rag-knowledge-assistant` project midway through an engagement. It has had one
live run on Bedrock, a baseline and calibrated judges, but no telemetry, CI or handover yet. Its representative set
is still the pack's starter set; the next step is to sample production traffic into it. The score counts the
checks that pass out of those that apply. `doctor` exits 1 until every check passes, so it can gate a "ready for
handover" step in CI.

| Doctor check | Passes when |
|---|---|
| offline demo | the suite runs on the simulator and mock judges |
| provider connected | a result exists from a run with a live (non-mock) provider |
| telemetry connected | an online result exists that read live telemetry (not a fixture) |
| report | that live run has an engineer report and scorecard, and the suite has a report template with failure categories |
| release gate | a blocking gate on a quality grader, a baseline in `baselines/`, and a CI workflow that runs genai-evals |
| eval set | a **representative** set of at least `min_representative_cases` (default 30) production-like cases, kept apart from the **stress** suite (edge / adversarial); every applicable coverage tag has a stress case; no blocking gate averages the two sets ([growing the eval set](growing-the-eval-set.md)) |
| judges calibrated | every judge-graded grader has a trusted calibration (or the pack has no judges); the detail says whether a representative validation sample was labelled too ([human labels](human-labels.md)) |
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
