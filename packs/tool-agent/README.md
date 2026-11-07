# Eval pack: tool-using agent

Evaluates an agent that acts on business systems through tools, whatever it is built on: OpenAI Agents SDK,
Bedrock AgentCore, Vertex AI Agent Engine (ADK), LangGraph or your own loop behind an API. The starter is an order
support agent; replace the tools, policy and cases with the customer's.

| | |
|---|---|
| **Measures** | the business outcome (final state), tool choice and arguments, call order, policy edges (forbidden actions), escalation, reliability across repeats (pass^k), steps, promises, privacy, latency |
| **You supply** | conversations with the expected outcome ([how](data/README.md)), a resettable sandbox or mock tools, and one target file pointing at the agent |
| **Gates** | reliable-success (pass^k), policy-edges (critical), right-tools, no-promises, privacy; steps and latency as warnings ([why](pack.yaml)) |
| **Report** | failure categories: wrong outcome, wrong tool or arguments, policy violation, missing escalation, inefficient, privacy, too slow |
| **Status** | runs offline in CI; no adapter is live tested yet ([adapters](../../docs/adapters.md)) |

## Quick start

```bash
genai-evals init my-agent-eval --pack tool-agent      # runs it offline: open out/offline/report.html
cd my-agent-eval
genai-evals experiment run experiment.yaml --out out/experiment   # a cheaper model and a trimmed prompt, compared
```

## Connect your agent

| File | Use when | Set in `.env` |
|---|---|---|
| `targets/tool-loop.yaml` | trying models and prompts on the mock tools before (or instead of) the real agent | `EVAL_TARGET_PROFILE` |
| `targets/http.yaml` | the agent is deployed behind an API; it must return its tool calls and sandbox state | `SYSTEM_URL`, `SYSTEM_TOKEN`, field names |
| `targets/agentcore.yaml` | Amazon Bedrock AgentCore Runtime | `AGENTCORE_AGENT_ARN`, `AWS_REGION` |
| `targets/agent-engine.yaml` | Vertex AI Agent Engine (ADK) | `AGENT_ENGINE_RESOURCE`, `GCP_PROJECT`, `GCP_LOCATION` |
| `targets/openai-agents-sdk.yaml` | an OpenAI Agents SDK agent you can import | `AGENT_FACTORY` |

Outcome graders need the state of the systems after each conversation: a sandbox that the target reports, or the
mock tools. Trajectory graders need the tool calls: from the target's response, or joined from telemetry by session.

## Connect telemetry

Keep the block for the customer's stack in `sources.yaml` and remove `fixture:`; `online_checks.yaml` checks promises,
privacy, step budgets and latency on production conversations. The offline judges flag the example phrases quoted
in each policy; real judges apply the whole policy text.

## Files

`suite.yaml` · `schema/case.schema.json` · `data/starter.jsonl` · `tools.py` mock tools · `targets/` ·
`prompts/support_agent` versioned prompt and tool schema · `providers.yaml` / `providers.live.yaml` ·
`sources.yaml` + `online_checks.yaml` · `experiment.yaml` · `labels/` · `RUNBOOK.md` · `HANDOVER.md` ·
`validation.yaml` · `sim_system.py`.
