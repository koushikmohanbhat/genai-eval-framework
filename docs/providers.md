# Providers and targets

## Provider profiles

All model access (targets, judges, the user simulator, embeddings) is declared once in `providers.yaml` and referenced
by name, so moving a suite between clouds means changing a profile name, not code.

| `provider` | API | Auth | Notes |
|---|---|---|---|
| `openai` | Responses API | `api_key_env` / `api_key_secret` | `store: false` by default; JSON schema output; reasoning and cached tokens recorded |
| `openai_compatible` | Chat Completions | optional key | LiteLLM, an AI gateway (catalog 6.2), vLLM, Azure-style gateways |
| `anthropic` | Messages API | key | tool use; cached tokens recorded |
| `vertex_anthropic` | Claude on Vertex AI | ADC | `project`, `location` |
| `bedrock` | Converse API | AWS chain: `aws_profile`, `role_arn`, instance / task role | any Bedrock model; Guardrails via `options.guardrail_id`; model family inferred from the id |
| `vertex` | Gemini via `google-genai` (vertexai=True) | ADC / impersonation | safety ratings are mapped to `metadata.guardrail` |
| `mock` | offline | none | `echo`, `scripted`, `judge` (heuristic judge for CI and demos) |

Common fields: `pricing` (USD per million input / output / cached-input tokens), `max_concurrency`, `rpm`, `retries`
(transient errors only: throttling, timeouts and 5xx), `timeout_s`, and `family` (an override used by the
judge-family policy).

**Secrets never live in YAML.** Inline `api_key` / `secret` / `token` values are rejected. Use `api_key_env`, or
`api_key_secret: aws-sm://<id>[#field]` / `gcp-sm://projects/<p>/secrets/<s>`.

`genai-evals check-config providers.yaml` fails on `store: true` and warns when pricing is missing.

## Targets (the system under test)

| `kind` / `framework` | What runs | Trajectory source |
|---|---|---|
| `model` / `prompt` | profile + versioned prompt (`prompt: id` or `id@1.2.0`) or inline `system` / `template` | n/a |
| `rag` | `retriever` (`python` callable, `bedrock_kb`, `vertex_rag`) + profile + prompt with `{{ context }}` and `{{ question }}` | retrieval step + context chunks |
| `agent` / `tool_loop` | provider-neutral agent loop on **any** profile; tools from the prompt's `tools.yaml`, implementations from `tools_module` | every tool call, args, result, latency; `final_state` from `make_state` |
| `agent` / `openai_agents_sdk` | `callable` returns an `agents.Agent`; run with `Runner.run` | tool calls, outputs, handoffs, usage |
| `agent` / `agentcore_runtime` | `InvokeAgentRuntime` on a deployed AgentCore agent | response only; join `aws_agentcore_observability` spans by session for the trajectory |
| `agent` / `bedrock_agent` | `InvokeAgent` with trace | action groups, KB lookups and references, usage, guardrail trace |
| `agent` / `agent_engine` | Vertex AI Agent Engine (ADK) `stream_query` | function calls and responses, usage |
| `http` | POST to a deployed endpoint; Jinja body; dotted-path `map` for output / context / trajectory / usage | mapped |
| `python` | `module:function(case, ctx)` returning text, a dict of Sample fields, or a Sample | as returned |

Multi-turn cases use scripted `turns`, or a `goal` + `persona` driven by the suite's `simulator` profile.

## Comparing providers on the same cases

```yaml
targets:
  - {name: openai,  kind: prompt, profile: openai-main,   prompt: claim_extractor, output: json}
  - {name: nova,    kind: prompt, profile: bedrock-nova,  prompt: claim_extractor, output: json}
  - {name: gemini,  kind: prompt, profile: vertex-gemini, prompt: claim_extractor, output: json}
```

The report puts each target on a quality vs cost chart with 95% CIs. The paired comparison tells you whether a
difference is real.
