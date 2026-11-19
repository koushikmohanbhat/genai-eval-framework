# Roadmap

Priority is the [definition of repeatable](engagement.md#definition-of-repeatable): reducing the custom work for the
next engagement comes before more connectors.

- **Live validation:** first live-tested records for one provider and one telemetry source per cloud, then fake
  clients for the remaining connectors' live paths in the conformance kit, and the shared retry helper in the Google
  Cloud connectors.
- **More packs:** contact-centre QA (transcripts), summarisation, classification / routing; agent pack overlays
  (insurance claims, banking service requests).

- **v0.8 voice / realtime:** OpenAI Realtime API and Gemini Live targets, user simulator over TTS, turn latency
  p50 / p95, barge-in, silence / overlap, ASR word error rate, recorded fixtures plus live mode
- **Live integration tests** on AWS and GCP sandbox accounts for every connector and target
- **Industry eval packs (catalog 4.2):** BFSI document extraction, insurance claims, contact-centre QA, medical summary
  hallucination, Indic-language quality. v0.7 shipped the `financial-services` overlay and the `us_hipaa` / `uk` /
  `banking` detector sets; v0.7.1 partner packages (entry points) with an SAP sales-order example. Next, healthcare
  claims, banking KYC (on the extraction pack) and retail support overlays, and the SAP example run live against
  the connector hub
- **Promptfoo engine:** a live model in the Promptfoo-engine CI job (it runs a real Promptfoo on a keyless target today),
  and HTTP agents through Promptfoo's `http` provider
- **Red-teaming (4.3):** the security-guardrails overlays (v0.6) are the curated part; next, more cases per threat,
  security overlays for the extraction pack, OWASP categories as a report section, and a Promptfoo red-team config
  generated from a suite
- **DPDP compliance pack (4.5):** cross-user leakage, refusal after consent withdrawal, "erased person is forgotten" tests
- **More targets:** Azure OpenAI / Foundry, Strands, CrewAI, LlamaIndex, MCP servers as tools under test
- **More sources:** Azure Monitor / Application Insights, AgentCore Gateway tool-call logs, Model Armor, LangSmith,
  Datadog LLM Observability, Arize, Kafka
- **Statistics:** cluster bootstrap, sequential testing for online A/B (`power` and Holm across gates shipped in v0.5.1)
- **Experiments:** variants across suites (e.g. retriever and prompt together as a grid), experiment history and trend view, link from a production prompt version back to the experiment that justified it
- **Active learning:** pick the most informative samples for human review; label UI
- **Seeding:** OCR and table extraction for scanned PDFs (with catalog 3.3), seeding agent goals from SOPs
- **Prompt optimisation loop** and gating for fine-tunes / distillation (catalog 7.2)
- **Streaming online evals** from collectors, with drift alerts (shared with 6.3)
- PyPI release; Helm / CDK packaging
