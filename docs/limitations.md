# Limitations (v0.3.0)

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

- **Voice / realtime** evaluation is not included yet (planned for v0.4).
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
