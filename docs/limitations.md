# Limitations (v0.1)

Read this before using the framework in production.

## Tested vs untested

- **Tested by unit tests and the offline demo:** the runner, all graders, statistics, gates, prompts-as-code,
  calibration, reports, the CLI, the OTel normaliser across OTLP / CloudWatch / X-Ray / Cloud Trace / Agents SDK /
  Langfuse shapes, every connector's fixture path, the online pipeline, Promptfoo export / import, EMF publishing and
  OTel emission.
- **Tested against fakes only (not live accounts):** request and response mapping for OpenAI Responses, Anthropic,
  Bedrock Converse, Vertex Gemini and OpenAI-compatible endpoints; the Agents SDK target; CloudWatch Logs pagination.
- **Written to the documented APIs but not yet run against live services:** AgentCore Runtime, Bedrock Agents, Agent
  Engine, Bedrock Knowledge Bases / Vertex RAG retrievers, X-Ray, AgentCore Memory, CloudWatch metrics, Cloud Trace,
  Cloud Logging, BigQuery, Cloud Monitoring, Agent Engine Sessions, Langfuse, and the OpenAI / Anthropic usage APIs.
  Run live integration tests in a sandbox account before relying on them.
- **Experimental (new or preview APIs; verify against your SDK version):** the `agentcore_evaluator` grader and the
  AgentCore code-based evaluator package (the request and event shapes may differ), the `vertex_metric` grader, Vertex
  prompt push, and AgentCore Evaluations / Vertex evaluation result import field names.

## Known gaps

- **Voice / realtime** evaluation is not in v0.1 (planned for v0.2).
- The mock judge is a heuristic for demos and CI. It is **not** a substitute for real judges.
- Bootstrap CIs assume cases are exchangeable. Clustered datasets (many cases per customer) need cluster-aware
  resampling (roadmap).
- Online evals run reference-free graders only. There is no ground truth for production traffic unless you add labels.
- Cost is computed from profile pricing tables that you maintain. Reconcile with the usage sources.
- The cache key covers configuration, the case and the prompt hash, not Python code. Python and agent targets are not
  cached by default.
- Regex PII detection misses names, addresses and free-text identifiers. Use the DPDP framework's cloud detectors for
  stronger coverage.
- `gcp_vertex_request_response` assumes default column names (`full_request`, `full_response`, `logging_time`, `model`).
  Override them with `columns:` if your table differs.
- AgentCore Runtime responses carry no trajectory. Join `aws_agentcore_observability` spans by session to get one.
