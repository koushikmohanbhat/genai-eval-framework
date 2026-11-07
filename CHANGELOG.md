# Changelog

## 0.3.0 (2026-11-07)

Makes the framework a repeatable asset: each engagement is easy to configure, run, validate and hand over, without
assuming the customer's cloud or agent stack.

### Eval packs (the repeatable unit)
- Three workflow packs in `packs/`, shipped in the wheel: `rag-knowledge-assistant`, `tool-agent`,
  `structured-extraction`. Each has a dataset schema with synthetic starter cases and guidance for customer cases,
  graders, failure categories, recommended gates with reasons, target files for each stack, provider and telemetry
  configuration with recorded OTLP telemetry, an experiment, a report template, a generated runbook and handover
  checklist, a validation file and an offline simulator.
- Optional industry overlays: `insurance-policy` (RAG) and `india-gst` (extraction). Suites gain `overlays`.
- `genai-evals init <dir> --pack <pack> [--overlay ...]` scaffolds a project and runs it offline, writing a sample
  report and a monitoring demo. Replaces the previous starter template.
- `genai-evals pack list | info | validate | docs`; `genai-evals doctor` checks a project against the definition of
  repeatable (offline demo, a live provider and telemetry source, an understandable report, a meaningful release
  gate, judges calibrated, handover complete, adapters validated).
- Suites gain `targets_from` (the system under test chosen by an env var), `dataset_schema` (rows validated on load,
  errors name the case and field), `extra_datasets`, `report` (title, intro, headline grader, failure categories),
  `validation` and `pack`. Reports show a failure-category table.

### Adapter contracts and conformance
- Six adapter kinds (provider, source, target, grader, store, report) with contract version 1, a catalog, and
  third-party plugins through entry points (`genai_evals.<kind>s`); plugins can't shadow built-ins and a contract
  major mismatch is refused.
- Conformance kit (`genai-evals conformance`, `assert_conformant`): formats, normalisation, determinism, missing
  fields, empty input, timestamps, pagination and retries for sources; normalisation, tool calls, retries and JSON
  mode for providers. Built-in specs and fixtures for every provider and 14 sources; untestable checks are reported
  as not covered, never as passes.
- Maturity levels: implemented, fixture tested, live tested, customer validated (the last two recorded per project
  with `genai-evals adapters record`). Results and reports list every adapter a run used with its effective status.
  `docs/adapters.md` is generated from the catalog and a conformance run.
- Shared retry helper for sources (`Source.call`), used by the CloudWatch Logs, AgentCore Memory and X-Ray paths.
- Fixes found by the kit: OTLP / OpenInference messages without a role no longer crash the normaliser; AWS sources
  no longer import boto3 when a client is injected; ISO timestamps with nanosecond or one-digit fractions (common in
  Google Cloud telemetry) are parsed on every supported Python, where 3.10 used to drop them.

### Portable results
- Results carry `schema: genai-evals/result` and `schema_version: 1.0`; the JSON Schema ships with the package
  (`genai-evals result schema | validate`). Pre-0.3 results are upgraded on read; newer majors are refused.
- `genai-evals result export --format csv,jsonl,promptfoo,...` (one row per score; Promptfoo results shape that
  round-trips with `promptfoo import`). `file://` results store (a folder plus an index), alongside SQLite and Postgres.

### Other
- `import_ref` prefers the suite folder's own modules, so several projects can run in one process.
- Docs: packs, engagement guide, plugins and conformance, adapters (generated), results; getting started rewritten
  around packs. Voice / realtime moves to v0.4.

## 0.2.0 (2026-11-06)

### Experiments (eval-driven iteration)
- `experiment.yaml`: a suite, the target to vary, a baseline and variants (prompt version, provider profile, options,
  retriever, any target field). All variants run in one run on the same cases, seed, repeats and judges.
- `genai-evals experiment run | rerun | report`. Output: `comparison.html/.md/.json`, `manifest.json`, a copy of the
  experiment file and the full run.
- Comparison per variant vs baseline: verdict (regresses / trade-off / improves / no detectable difference),
  per-grader Δ with intervals and McNemar, pass^k, new failures and fixes; cost and latency (mean, p95, relative,
  paired Δ); critical cases newly failing or fixed; slices ranked by change (under `min_n` listed, not ranked);
  failure types and flipped cases; quality vs cost and latency chart.
- Multiple comparisons: Bonferroni across variants × (graders + cost + latency) by default, with intervals shown at
  the corrected level; `correction: none` to opt out.
- Reproducibility: the manifest records hashes of the experiment, suite, providers, dataset, calibration, local target
  code and every prompt version used; providers used (no secrets); judge provenance; env var names; seed, repeats;
  framework / Python version and git commit. `rerun` refuses when inputs changed (`--force` to override) and reports
  whether each comparison reproduced.
- `genai-evals compare` and `prompts diff` now produce the same comparison (`compare --out`, `--fail-on-regression`).
- Demo scenario 3b and a sample comparison page; Python targets receive the target's prompt (`ctx["prompt"]`).

Voice / realtime moves to v0.3.

## 0.1.1 (2026-11-06)

Fixes from an external review. Several change results: re-run baselines after upgrading.

### Gates and statistics (behaviour changes)
- Gates have three outcomes: pass, fail and **invalid**. Invalid blocking gates fail the run.
- `delta()` / `significant()` without a baseline make the gate invalid instead of passing. Use
  `--allow-missing-baseline` (CLI and GitHub Action) or `allow_missing_baseline: true` on a gate for a first run.
  A `--baseline` file that doesn't exist is now an error (exit 2).
- Target and grader errors count as failed scores (value 0) by default (`suite.on_error: fail`; `exclude` restores the
  old behaviour for diagnosis).
- `suite.min_n` (default 5, overridable per gate): gates on too few cases, tag slices or baseline overlap are invalid.
- `significant(g, alpha=...)` for multiple-comparison corrections.

### Judges and calibration
- A judge is trusted only with at least `min_calibration_n` labels (default 30) and the **lower bound** of a bootstrap
  95% CI on κ at or above `min_judge_kappa`. Calibration also reports human pass rate, positive / negative agreement,
  balanced accuracy and prevalence warnings. `genai-evals calibrate --min-n` and a non-zero exit when untrusted.
- Results record judge provenance (prompt template hash, temperature, response format, policy, per-judge provider,
  configured and reported model ids, family, region, options) and a config hash per grader.

### DPDP
- Error strings, sample metadata and score reasoning / details / errors / labels are now tokenised before storage.
- The response cache is off by default when tokenisation is on (it would hold raw outputs). Opting in warns and
  writes the cache with owner-only permissions.
- A warning when `GENAI_EVALS_TOKEN_KEY` is unset (tokens then differ between runs); results record data handling.

### Online evals
- Sampling is deterministic and per session (hash of the session / trace id), and the result records graded vs read
  counts, rate and window per source.
- Online reports and scorecards are framed as production monitoring (reference-free signals), not a release verdict.
- Reports state the scope of `pii_leak` (structured identifiers only).

### Docs
- Integration maturity labels and an explicit "nothing live-tested yet" statement; gate outcomes and exit codes;
  statistical assumptions (exchangeability, multiple comparisons, small *n*); the security boundary for Python / HTTP
  code; a step-by-step DPDP data-flow table; what online scores do and don't mean.

## 0.1.0 (2026-11-06)

### Partner setup
- No environment-specific settings in the repo: every YAML value can come from `${VAR}`, `${VAR:-default}` or
  `${VAR-default}`; missing variables are reported by name. `.env` is loaded automatically (`--env-file` to choose);
  existing environment variables win.
- `genai-evals init <dir>`: scaffolds a starter evals project for a partner's or customer's own repo (offline and
  live providers, suite, sources, online checks, dataset, versioned prompt, `.env.example`, GitHub Actions workflow).
- Example live configs, Terraform (no region defaults, `environment` map, `terraform.tfvars.example`) and the
  Dockerfile (no suites baked in) no longer carry environment-specific values.
- New docs: getting started, configuration reference, AWS / Google Cloud / OpenAI and Anthropic setup,
  troubleshooting; expanded Terraform guide. Install instructions point at GitHub until a PyPI release.

### See it
- Sample reports from the offline demo in `docs/demo/` (served with GitHub Pages), README screenshots, an
  architecture diagram, a 5-minute walkthrough, and a Codespaces dev container that runs the demo.
  `scripts/build_demo_site.py` regenerates the pages and screenshots.

### Core
- Evals as code: suites, datasets (JSONL / JSON / CSV / Parquet), targets, graders, gates.
- Providers: OpenAI Responses, OpenAI-compatible, Anthropic (direct / Vertex), Bedrock Converse, Vertex Gemini, mock.
- Targets: prompt / model, RAG (Bedrock KB, Vertex RAG, Python), tool-loop agent, OpenAI Agents SDK, AgentCore Runtime, Bedrock Agents, Agent Engine, HTTP, Python.
- 37 graders incl. judge panels, calibration, pairwise with position swap, Indian-format field normalisers, PII leak.
- Statistics: bootstrap CIs, pass@k / pass^k, paired deltas, McNemar, slices, critical cases. Gate DSL.
- Prompts as code: versions, hashes, lock stages, gated promotion, OTel span stamping, registry mirror, DPDP change detection.
- Native sources (AWS, Google Cloud, OpenAI, Anthropic, OTLP, Langfuse), OTel GenAI normaliser, online evals.
- Reports (HTML, scorecard, Markdown, JUnit, JSON); publishing (CloudWatch, EMF, Cloud Monitoring, OTLP); OTel emission.
- Interop: Promptfoo export / import, AgentCore Evaluations, Vertex Gen AI evaluation.
- Offline insurance demo; GitHub Action; Terraform for AWS and GCP; ADOT and Google collector configs.
