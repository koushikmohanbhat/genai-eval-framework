# Changelog

## 0.1.0 (2026-11-06)
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
