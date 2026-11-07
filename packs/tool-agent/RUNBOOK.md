# Runbook: Tool-using agent

The path from a clean project to a customer-owned evaluation, for the `tool-agent` pack (0.1.0).
Each step lists what to run and what "done" looks like. `genai-evals doctor` checks steps 1 and 4 to 9.

## 1. Run it offline (day 0, 10 minutes)

```bash
pip install "genai-eval-framework[openai,aws,gcp]"
genai-evals init my-eval --pack tool-agent     # already done if you are reading this in your project
cd my-eval
genai-evals run suite.yaml --out out/offline --allow-missing-baseline
```

Done when `out/offline/report.html` opens and shows every gate. It uses the simulated system and mock judges:
nothing leaves your machine.

## 2. Agree what "good" means (workshop with the customer)

Walk the customer's product owner and SMEs through the sample report and decide:

- **Failure categories** that matter (the pack's defaults):
  - **wrong outcome**: the systems end in the wrong state (task_success)
  - **wrong tool or arguments**: a required call is missing, has wrong arguments, or happens out of order (correct_calls, lookup_first)
  - **policy violation**: an action the policy forbids, or a promise it can't keep (no_refund_*, no_address_change_after_ship, no_promises)
  - **missing escalation**: should have handed over to a person (escalated)
  - **inefficient**: too many steps or repeated calls (steps)
  - **privacy**: personal identifiers echoed back (no_pii)
  - **too slow**: over the latency budget (latency)
- **Critical cases**: scenarios that must never fail, whatever the averages say.
- **Gate thresholds**: start from the pack's gates (below) and adjust to the customer's risk appetite.

Record the decisions in `HANDOVER.md` (owners) and `suite.yaml` (gates).

## 3. Build the dataset

Follow [`data/README.md`](data/README.md). Validate as you go:

```bash
genai-evals validate suite.yaml          # schema errors name the case id and the field
```

Done when the dataset has the agreed slices and critical cases, SMEs have reviewed it, and personal data is tokenised.

## 4. Connect the customer's system

Pick the target file that matches their stack, fill its variables in `.env` (copy `.env.example`), and switch to live
providers:

| Target file | System |
|---|---|
| `targets/offline.yaml` | simulated agent on mock tools (offline) |
| `targets/tool-loop.yaml` | any model running the versioned prompt and tool schema on the mock tools |
| `targets/http.yaml` | any deployed agent behind HTTP(S) that reports its tool calls and state |
| `targets/agentcore.yaml` | an agent on Amazon Bedrock AgentCore Runtime |
| `targets/agent-engine.yaml` | an ADK agent on Vertex AI Agent Engine |
| `targets/openai-agents-sdk.yaml` | an agent built with the OpenAI Agents SDK |

```bash
# .env
EVAL_TARGETS=targets/tool-loop.yaml
GENAI_EVALS_PROVIDERS=providers.live.yaml
EVAL_JUDGE_A=judge-openai
EVAL_JUDGE_B=judge-gemini
```

```bash
genai-evals check-config providers.live.yaml
genai-evals run suite.yaml --out out/live-1 --allow-missing-baseline
```

Done when the run completes against the real system and the report's "Adapters used" table lists the live provider.
Keep only the profiles and targets the customer uses.

## 5. Calibrate the judges

Follow [`labels/README.md`](labels/README.md): at least 30 SME labels per judge-graded grader, then
`genai-evals calibrate`. Done when every judge-graded gate is trusted (no *invalid* gates from calibration).

## 6. Set the release gate and baseline

```bash
genai-evals run suite.yaml --out out/main --allow-missing-baseline
genai-evals baseline promote out/main/result.json --out baselines/main.json
```

Copy `ci/github-actions-evals.yml` into the customer's repository (or port the two commands to their CI: the exit code
is the gate). Done when a pull request runs the suite against `baselines/main.json` and a deliberate regression fails it.

Recommended gates for this pack:

| Gate | Rule | Why |
|---|---|---|
| reliable-success | `pass_hat_k(task_success) >= 0.8` | succeeds on every repeat, not just on average |
| policy-edges | `critical_pass()` | over-limit refunds, shipped orders, safety and legal escalations never fail |
| right-tools | `mean(correct_calls) >= 0.9` | the right systems are touched with the right arguments |
| no-promises | `mean(no_promises) >= 1.0` | no commitments the business can't keep |
| privacy | `mean(no_pii) >= 1.0` | no personal identifiers repeated back |
| efficient (warn) | `mean(steps) >= 0.9` | cost and latency grow with steps |
| latency-budget (warn) | `p95(latency_ms) <= 8000` | set from the product's SLO |

## 7. Connect telemetry (production monitoring)

Keep the customer's source in `sources.yaml`, remove `fixture:`, and run:

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --out out/online
```

Add `--publish cloudwatch` or `--publish cloud_monitoring` to alarm on scores. Done when a scheduled run grades
real sessions and an alert reaches the team that owns the system.

## 8. Change things with experiments

For a new prompt, model or retriever setting, describe it as a variant in `experiment.yaml` and run
`genai-evals experiment run experiment.yaml`. The comparison shows quality, cost, latency and critical cases side by
side, and the manifest makes it reproducible.

## 9. Record validation and hand over

```bash
genai-evals adapters record --kind provider --name <provider> --status live_tested --environment "<where>" --evidence out/live-1/result.json
genai-evals doctor
```

Fill in [`HANDOVER.md`](HANDOVER.md) with the customer and walk through it together. Done when `genai-evals doctor`
passes and the customer has run the suite themselves.
