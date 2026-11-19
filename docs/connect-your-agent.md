# Connect the customer's agent in 30 minutes

Most customers have their own orchestrator: LangGraph, LlamaIndex, CrewAI, a custom FastAPI service. You don't
need a plugin for any of them. Pick one of three routes, fastest first.

## 1. Decorate the function that answers a message (Python, same process)

```python
import genai_evals

@genai_evals.tool                       # on each tool the agent calls: records name, arguments, result
def lookup_order(order_id: str) -> dict: ...

@genai_evals.tool
def issue_refund(order_id: str, amount: int) -> dict:
    genai_evals.report_state(refunds=1, refunded_amount=amount)    # the outcome final_state checks (optional)
    ...

@genai_evals.agent
def support_agent(message: str, session: dict, user: str | None = None) -> str:
    ...                                 # the customer's existing code; `session` persists across a conversation
    return reply
```

```yaml
targets:
  - {name: support, kind: python, callable: "my_agent:support_agent"}
```

The function can also take `history`, `turn` and `session_id`, and return a dict (`output`, `tool_calls`, `state`,
`usage`, `cost_usd`) instead of a string. Each user turn of a [multi-turn case](multi-turn.md) is one call.
LangGraph: pass `session_id` as the thread id so its checkpointer keeps the conversation
([example](../examples/connect-your-agent/langgraph_agent.py)).

## 2. Serve it with the genai-evals contract (HTTP)

When the agent runs in its own service, expose one extra route that speaks this contract, or let genai-evals serve
the decorated function:

```python
genai_evals.serve(support_agent, port=8080)                 # stdlib server, for eval runs
# or, inside the customer's FastAPI app, behind its normal auth:
@app.post("/evals/chat")
async def evals_chat(req: dict) -> dict:
    return await support_agent.ahandle(req)
```

```yaml
targets:
  - {name: support, kind: http, options: {url: "https://.../evals/chat", contract: v1, headers_env: {Authorization: EVAL_TOKEN}}}
```

| | Field | |
|---|---|---|
| Request | `message` | the user's message for this turn |
| | `session_id` | the same for every turn of a conversation |
| | `turn`, `case_id` | turn number, eval case id |
| | `user` | the signed-in user the case says is asking (`metadata.user` / `metadata.customer`), for ownership checks |
| Response | `output` | **required**: the reply |
| | `tool_calls` | `[{name, args, result}]` |
| | `state` | the business outcome so far (`final_state`) |
| | `session_id` | if the service issues its own; later turns send it back |
| | `usage`, `cost_usd`, `trace_id` | tokens, cost, the trace to link to |

## 3. Map an endpoint you can't change (HTTP, no code)

Template the request and map the response fields with `body` and `map` ([configuration](configuration.md)).

## What you lose without tool calls

If the agent reports no tool calls (no `@genai_evals.tool`, no `tool_calls` in the response), only its replies are
graded: tool, trajectory and final-state graders are skipped for it, and reports say so. That's often enough for a
first POC (`--profile quickstart`); add tool reporting before a release gate.
