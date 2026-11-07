# Adding customer conversations

Each line is one conversation, validated against [`schema/case.schema.json`](../schema/case.schema.json) when the
suite loads.

```json
{"id": "ref-031", "input": "Refund ORD-20011, it arrived broken.", "tags": ["refund"],
 "reference": {"final_state": {"refunds": 1, "escalated": false},
               "tool_calls": [{"name": "lookup_order", "args": {"order_id": "ORD-20011"}}],
               "tool_sequence": ["lookup_order", "issue_refund"]}}
```

- **`final_state` is the business outcome**: the part of the systems' state after the conversation that matters
  (refund issued or not, ticket raised, address changed). It is a subset match, so list only what the case is about.
- **`tool_calls`** are calls that must happen with those arguments (subset match); **`tool_sequence`** is an order that
  must hold. Leave them out when any route to the outcome is fine.
- **Multi-turn**: use `turns` for scripted user turns, or `goal` + `persona` for the user simulator.
- **Policy edges** (over limits, wrong status, safety, legal) are `critical: true` and tagged, so the policy graders
  (`tags: [over-limit]` etc.) run on them.
- **How many.** 50–150 conversations per agent: every policy edge at least 5 times, every intent at least 10, with 3–5
  repeats so `pass^k` is meaningful.
- **Where they come from.** Policy owners write the edges; real conversations (tokenised, `source: production`)
  cover the rest. Production failures from `genai-evals online` become cases.
- **The state** comes from `tools.py` offline, or from a sandbox for a deployed agent (the target must return it).
