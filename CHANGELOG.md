# Changelog

## Unreleased
- No environment-specific settings in the repo: every YAML value can come from `${VAR}`, `${VAR:-default}` or
  `${VAR-default}`; missing variables are reported by name. `.env` is loaded automatically (`--env-file` to choose);
  existing environment variables win.
- `genai-evals init <dir>`: scaffolds a starter evals project for a partner's or customer's own repo (offline and
  live providers, suite, sources, online checks, dataset, versioned prompt, `.env.example`, GitHub Actions workflow).
- Example live configs, Terraform (no region defaults, `environment` map, `terraform.tfvars.example`) and the
  Dockerfile (no suites baked in) no longer carry environment-specific values.
- New docs: getting started, configuration reference, AWS / Google Cloud / OpenAI and Anthropic setup,
  troubleshooting; expanded Terraform guide. Install instructions point at GitHub until a PyPI release.

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
