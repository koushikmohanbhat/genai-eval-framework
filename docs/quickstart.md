# Quickstart: a deployed endpoint in 15 minutes

For the first meeting with a client: point genai-evals at their HTTP endpoint and get a first, directional read.
No Terraform, telemetry, judge calibration or pack setup.

```bash
pip install "genai-eval-framework[xlsx]"
export SYSTEM_TOKEN=...                                    # if the endpoint needs auth; never written to a file
genai-evals quickstart --url https://assistant.client.example/chat \
  --header Authorization=SYSTEM_TOKEN \
  --cases questions.csv                                   # optional, but it's what makes the numbers mean something
```

What it does, in order:

1. **Probes** the endpoint with one request and reads the response: where the answer is, and whether tool calls,
   state or retrieved sources come back. It prints its reading; if the answer path is wrong, pass `--map-output
   data.reply`. If the request body isn't `{"message": ...}`, pass `--body '{"query": "{{ input }}"}'`. Endpoints
   that speak the [contract](connect-your-agent.md) are detected and need no mapping.
2. **Writes a project** (`quickstart-eval/`: `suite.yaml`, `cases.jsonl`, `.env` with the URL). Header values stay in
   your shell.
3. **Runs it** with one repeat and warn-only gates, and prints what was flagged.

## With and without the client's questions

| | Without `--cases` | With `--cases` |
|---|---|---|
| Measures | only domain-agnostic behaviour: it answers; it doesn't follow instructions planted in pasted text (3 probes); no card, Aadhaar, PAN, phone or email numbers it wasn't given; latency | that, plus the client's task: answers contain the facts they said must be there (`must_contain`), and with `--judge <profile>` agree with `expected` |
| Means | "is it basically safe to point at", not "is it good" | a directional quality read on their questions |

`questions.csv` (or `.xlsx`, `.jsonl`) needs a question column (`input`, `question`, `prompt` or `query`) and
optionally `expected`, `must_contain` (`;`-separated), `tags` and `critical` (`yes`). Ten to thirty real questions
from the client's SME are enough for a first read; the [SME labelling page](human-labels.md) helps them write and
check them.

## What it doesn't do

- It's **directional**: one repeat, warn-only gates, one uncalibrated judge at most. Don't present it as sign-off.
- **No tool calls in the response** means outcomes and tool use aren't checked. Wrap the agent with `@agent` /
  `@tool` or return `tool_calls` and `state` ([connect your agent](connect-your-agent.md)).
- It doesn't read production telemetry. For that, run `genai-evals sources preflight sources.yaml` first
  ([sources](sources.md#preflight-before-the-first-online-run)).

## Where the 15 minutes go

Measured on a local test endpoint: the probe, project and run take seconds. On a real engagement the time goes to
getting a URL and a token from the client (have them ready before the meeting) and to the client's questions. The
run itself is 7 probes plus your questions, one call each.

## Next: from quickstart to sign-off

Move to the pack for the workflow (`genai-evals init <project> --pack rag-knowledge-assistant | tool-agent |
structured-extraction`), set `targets/http.yaml` to the mapping the quickstart printed, and follow the pack's
runbook: the client's cases, calibration, the `release-gate` profile and the handover checklist.
