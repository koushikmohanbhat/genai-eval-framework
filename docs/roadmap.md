# Roadmap

- **v0.2 voice / realtime:** OpenAI Realtime API and Gemini Live targets, user simulator over TTS, turn latency
  p50 / p95, barge-in, silence / overlap, ASR word error rate, recorded fixtures plus live mode
- **Live integration tests** on AWS and GCP sandbox accounts for every connector and target
- **Industry eval packs (catalog 4.2):** BFSI document extraction, insurance claims, contact-centre QA, medical summary
  hallucination, Indic-language quality
- **Red-teaming (4.3):** Promptfoo red-team plugins through the framework; OWASP LLM Top 10 mapping in reports
- **DPDP compliance pack (4.5):** cross-user leakage, refusal after consent withdrawal, "erased person is forgotten" tests
- **More targets:** Azure OpenAI / Foundry, Strands, CrewAI, LlamaIndex, MCP servers as tools under test
- **More sources:** Azure Monitor / Application Insights, AgentCore Gateway tool-call logs, Model Armor, LangSmith,
  Datadog LLM Observability, Arize, Kafka
- **Statistics:** cluster bootstrap, sequential testing for online A/B, power analysis helper
- **Active learning:** pick the most informative samples for human review; label UI
- **Prompt optimisation loop** and gating for fine-tunes / distillation (catalog 7.2)
- **Streaming online evals** from collectors, with drift alerts (shared with 6.3)
- PyPI release; Helm / CDK packaging
