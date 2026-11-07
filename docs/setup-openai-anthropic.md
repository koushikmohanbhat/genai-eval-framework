# OpenAI and Anthropic (direct) setup

## Keys

Keys are read from environment variables or a secret manager, never from YAML:

```yaml
profiles:
  openai:
    provider: openai
    model: "${OPENAI_MODEL}"
    auth: {api_key_env: OPENAI_API_KEY}              # or api_key_secret: aws-sm://... / gcp-sm://...
    options: {store: false}
  anthropic:
    provider: anthropic
    model: "${ANTHROPIC_MODEL}"
    auth: {api_key_env: ANTHROPIC_API_KEY}
```

- Use a **dedicated project / workspace and key for evals**, with its own spend limit, so eval traffic is easy to
  attribute and can't exhaust production rate limits. Set `max_concurrency` and `rpm` on the profile to stay
  inside the limits.
- In CI, store keys as CI secrets and expose them as environment variables (see `ci/github-actions-evals.yml` from
  `genai-evals init --pack <pack>`).
- Pin model snapshots in `.env` (`OPENAI_MODEL`, `ANTHROPIC_MODEL`, judge models), so a provider-side model update
  shows up as a deliberate change, not a silent regression.

## Data handling

- **OpenAI**: the framework calls the Responses API with `store: false` by default, and `genai-evals check-config`
  fails if a profile sets `store: true`. Abuse-monitoring retention and zero data retention follow your OpenAI
  agreement.
- **Anthropic**: retention follows your Anthropic agreement (zero data retention by arrangement).
- Inputs and outputs are tokenised for Indian identifiers before they are sent to **judges** and stored. The system
  under test receives the case input as written, so keep personal data out of golden sets or synthesise it
  ([DPDP](dpdp.md)).

## Agents built with the OpenAI Agents SDK

- **As a target**: `{kind: agent, framework: openai_agents_sdk, callable: "my_app.agents:build_agent"}` (install the
  `agents` extra). The run's tool calls, handoffs and usage become the trajectory.
- **Production traces for online evals**: register the trace processor in the application, then read the file (or
  ship it to your log store) with the `openai_agents_trace` source:

  ```python
  from agents import add_trace_processor
  from genai_evals.sources import GenAIEvalsTraceProcessor
  add_trace_processor(GenAIEvalsTraceProcessor("traces/agents.jsonl"))
  ```

## Usage and cost reconciliation (optional)

The `openai_usage` and `anthropic_usage` sources read the organisation usage APIs, which need **admin keys**. Keep
them separate from inference keys, read-only where possible, and pass them through `admin_key_env`.

## Langfuse (optional)

If the customer already traces with Langfuse, use the `langfuse` source and set `LANGFUSE_HOST`,
`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
