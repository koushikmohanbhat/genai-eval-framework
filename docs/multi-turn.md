# Multi-turn conversations

Support agents rarely fail on a single tool call. They fail three turns in: they forget which order was being
discussed, act on the number the customer just corrected, or give in when the customer pushes back. Multi-turn cases
test that.

## Three ways to write a conversation

```jsonl
{"id": "c1", "input": "Has ORD-10041 been delivered?", "turns": ["Has ORD-10041 been delivered?", "Great, then refund it."]}
{"id": "c2", "input": "I want a refund.", "user_rules": [{"when": "order number", "say": "It's ORD-10045."}, {"otherwise": "[DONE]"}], "max_turns": 4}
{"id": "c3", "goal": "Get a refund for a damaged blender; you don't remember the order number at first", "persona": "busy, a bit impatient"}
```

| | How the user's next message is chosen | Reproducible | Use for |
|---|---|---|---|
| `turns` | a script | yes | corrections, references back ("it", "that order"), topic switches |
| `user_rules` | the first unused rule whose `when` regex matches the agent's last reply; `otherwise` when none does | yes | answering the agent's questions, pushing back, changing their mind; runs offline and in CI |
| `goal` (+ `persona`) | a model playing the customer (suite `simulator: <profile>`) | no: use `repeats` | open-ended conversations, realistic variety |

Scripted turns run first, then the rules or the model. A conversation ends when the simulated user says `[DONE]`
(or has nothing more to say) or at `max_turns` (default: the scripted turns, else 6); hitting the limit while the
user still had something to say is recorded (`metadata.max_turns_reached`).

## Which targets hold a conversation

Every agent target drives the conversation turn by turn and keeps one session:

| Target | Session kept by |
|---|---|
| `http` | `{{ session_id }}` in the body (default body: `{"input": "{{ message }}", "session_id": "{{ session_id }}"}`; `conversation_body:` to override; also `{{ turn }}`, `{{ history }}`). If the response maps a `session_id`, later turns send that one |
| `python` | `ctx["session"]`, a dict kept for the conversation, plus `ctx["history"]`, `ctx["turn"]`, `ctx["session_id"]`. The callable opts in with `fn.per_turn = True` (or `options: {per_turn: true}`); without it, it gets the whole case once, as before |
| `agent` / `tool_loop` | the message list |
| `agent` / `openai_agents_sdk` | the SDK's input list (`to_input_list()`) carried into the next `Runner.run` |
| `agent` / `agentcore_runtime` | one `runtimeSessionId` for every turn |
| `agent` / `bedrock_agent` | one `sessionId` (Bedrock keeps the session's memory) |
| `agent` / `agent_engine` | one Agent Engine session (`create_session`, then `stream_query(session_id=...)`) |

Every tool call records the `turn` it happened in, the sample keeps the whole transcript (`messages`), and latency
is the agent's own time summed over turns (`metadata.turn_latency_ms` per turn; simulated-user time excluded).

## Grading a conversation

- **`final_state`, `tool_args_match`, `tool_not_called`** work as for one turn, over the whole conversation.
- **`context_retained`**: did the agent act on what was said earlier, and not on what was corrected?

  ```json
  "reference": {"context": {
    "use":       [{"name": "update_address", "args": {"order_id": "ORD-10044"}, "after_turn": 2}],
    "never_use": [{"name": "update_address", "args": {"order_id": "ORD-10043"}}]}}
  ```

  It is skipped, not passed, when the target ran only one turn: a target that can't hold a conversation shouldn't
  look as if it never drifts.
- **`per_turn: true`** on rubric and policy judges grades every assistant turn, so one bad turn fails the
  conversation ([agent evals](agent-evals.md)).
- **`step_budget`** applies per user turn in a conversation (looking the order up again in turn 3 isn't waste);
  `per: conversation` for the old behaviour.

## In the tool-agent pack

Six starter conversations (`mt-*`): the agent has to ask for the order number and use the answer; a correction
("not ORD-10043, it's ORD-10044"); "refund it" after a status question; a change of mind; a four-turn conversation
that returns to "its delivery address"; a customer pushing for a refund the agent must still escalate. The gate
`multi-turn-context` requires `context_retained` on all of them. The offline agent's `forgetful: true` option drops
context between turns, and fails it.
