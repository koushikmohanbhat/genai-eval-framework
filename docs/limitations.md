# Limitations (v0.6.2)

Read this before using the framework in production.

## Tested vs untested

**Live tested today: no adapter.** Built-in adapters are at most *fixture tested*: they pass the
[conformance kit](plugins.md#conformance) on recorded data and fake clients in CI. Projects record *live tested* and
*customer validated* in their `validation.yaml`, and reports show the result. The full matrix is in
[adapters](adapters.md). The first live run in a partner sandbox is the most useful contribution you can make;
please open an issue with what you find.

- **Fixture tested (conformance kit):** every provider (normalisation, tool calls, retries, JSON mode) and 14 of the
  17 sources (formats, normalisation, determinism, missing fields, empty input, timestamps).
- **Live-path pagination and retry checks** cover only the CloudWatch Logs connectors (AgentCore Observability,
  Bedrock invocation logs, AgentCore Evaluations) and AgentCore Memory. The other connectors' live paths (X-Ray,
  Cloud Trace, Cloud Logging, BigQuery, Cloud Monitoring, Agent Engine Sessions, Langfuse) have no fake client in
  their specs yet and are reported as *not covered*; Google Cloud connectors also don't use the shared retry helper
  yet.
- **Implemented, not tested:** the OpenAI / Anthropic usage sources, Vertex evaluation import, and the AgentCore
  Runtime, Bedrock Agents and Agent Engine targets.
- **Experimental (preview APIs; verify against your SDK version):** the `agentcore_evaluator` grader and evaluator
  package, the `vertex_metric` grader, Vertex prompt push, AgentCore Evaluations / Vertex evaluation result import.
- **Packs:** all three run offline in CI (validate, scaffold, offline run, monitoring on recorded telemetry). Their
  simulators and starter cases show the shape of an evaluation; they say nothing about any real system.

## Known gaps

- **The representative set is only as representative as its sample.** Weights correct for known over- or
  under-sampling, not for traffic the sample never saw. Re-sample it from production regularly. The starter packs'
  representative sets are synthetic and below the 30-case minimum on purpose.
- **Provenance is recorded, not audited.** `doctor` checks that a `representative_sample:` record exists and that
  most representative cases say `source: production`. It can't verify that the record is true; `cases sample` makes
  it true by construction when the set is drawn from an online run.
- **Stress minimums are rules of thumb.** 10 cases for a high-risk tag and 5 otherwise are enough to see a gross
  problem, not to estimate a rate precisely (with 10 cases, a 95% interval on a pass rate is roughly ±30 points).
  Raise them for tags that decide releases.
- **Production-weighted agreement depends on the sheet's weights.** They describe the result the sheet was drawn
  from; if that result was a curated suite rather than production traffic, the "production" estimate inherits the
  curation. Use the random validation sample from the representative set as the check.

- **Length bias is measured, not corrected.** Judges are told not to reward length and `pairwise` reports how often
  the longer answer won, but no score is adjusted for length. Read `length_bias` and act on it (shorter answers in
  the dataset's references, a stricter rubric, another judge).
- **Generated and harvested cases are only drafts.** `cases generate` is as good as the generator model and the
  examples it sees; `cases harvest` re-tokenises with regex detectors, which miss names and addresses. Neither
  counts until a person has reviewed it.
- **The judge ladder needs labels.** Non-inferiority on κ with a 0.05 margin usually needs 150–200 labelled units on
  the sample that decides (the random validation sample, when given); with fewer the cheaper judge is (correctly)
  not approved.
- **Security overlays are starter sets.** 3 cases per threat show the shape; a sign-off needs more, on the
  customer's own documents and records, plus a broad red-team scan (Promptfoo) and an application security review.
  Indirect-injection cases only test a live system if it can read planted test data ([security](security.md)).
- **Security graders are exact, and narrow.** Canary and copied-text checks miss paraphrased leaks (the overlays add
  a `warn` judge for those), and marker checks miss injections that act differently; pair them with final-state,
  tool-trace and faithfulness checks ([security](security.md#what-a-pass-shows-and-what-it-doesnt)).
- **Seeding flags are heuristics.** `source_contains_instructions` and `answer_not_found_in_passage` use keyword
  rules; a reviewer still reads every draft.
- **Seeded cases are synthetic.** `cases seed` questions come from the documents, so they skew easy and share their
  wording; they don't count as the representative set. PDF tables and scans are read as plain text only.
- **Sample-size numbers are planning estimates.** `genai-evals power` uses the normal approximation for pass/fail
  rates and assumes independent cases; it doesn't account for clustering or partial-credit scores.
- **Holm correction covers `significant()` only.** Thresholds on means and CI bounds are not p-values and aren't
  corrected; slices are not part of the family.
- **Sensitivity and checklists cost judge calls:** one re-grade of every unit per variant, and one call per criterion.
- **Reference checks only cover what a reference can stand in for.** `cases check-refs` can't check graders that
  need latency, retrieval beyond the gold passages, or trace details, and it runs judges only with `--judges`.
- **Per-turn grading costs one judge call per turn per judge.** Long conversations are capped at `max_turns`.
- **Judge error classes come from the error text.** Transient (rate limit, timeout, 5xx, connection) vs persistent is
  decided by the same keyword rules as provider retries. An SDK that words a transient error unusually is treated as
  persistent, which is the safe side: it counts against the judge until retried with `regrade --only errors`.
- **Weights only fix known, random capture.** `cases sample` weights by telemetry capture rate, online sampling and
  selection. If `capture_rate` is not set, the set represents the captured telemetry only; and capture that drops
  sessions for a reason (errors, an uninstrumented channel) is bias no weight corrects.
- The mock judge's heuristics for the context graders and case generation exist so packs run offline; they say
  nothing about how a real judge or generator would score.

- **Voice / realtime** evaluation is not included yet (planned for v0.7).
- The mock judge is a heuristic for demos and CI (for policy graders it only flags the example phrases quoted in the
  policy). It is **not** a substitute for real judges.
- Bootstrap CIs assume cases are exchangeable. Clustered datasets (many cases per customer) need cluster-aware
  resampling (roadmap). There is no automatic multiple-comparison correction; see
  [statistical assumptions](gates.md#statistical-assumptions).
- Online evals run reference-free graders only. There is no ground truth for production traffic unless you add labels,
  and only captured traffic can be graded ([what online scores mean](sources.md#what-online-scores-mean)).
- Experiments reproduce within their intervals, not exactly: provider models and sampling are not deterministic, and
  code outside the suite folder is pinned only by the git commit ([reproducibility](experiments.md#reproducibility)).
  Bonferroni correction is conservative with many graders; compare fewer graders for more sensitivity.
- `python` / `http` targets and graders run with your credentials and network access
  ([security boundary](architecture.md#security-boundary)).
- Cost is computed from profile pricing tables that you maintain. Reconcile with the usage sources.
- The cache key covers configuration, the case and the prompt hash, not Python code. Python and agent targets are not
  cached, and nothing is cached when tokenisation is on unless a target opts in.
- Regex PII detection misses names, addresses and free-text identifiers. Use the DPDP framework's cloud detectors for
  stronger coverage.
- `gcp_vertex_request_response` assumes default column names (`full_request`, `full_response`, `logging_time`, `model`).
  Override them with `columns:` if your table differs.
- AgentCore Runtime responses carry no trajectory. Join `aws_agentcore_observability` spans by session to get one.
