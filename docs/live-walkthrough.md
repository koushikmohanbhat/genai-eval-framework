# Live walkthrough: a failure caught on real infrastructure

A script and shot list for a 5-minute recording that shows genai-evals catching a real regression with a real
model on AWS or Google Cloud. It complements the offline terminal recording, and the same run gives the first
**live tested** adapter record (`validation.yaml`).

**What it shows:** the tool-agent pack's support prompt runs on Bedrock or Vertex against the pack's mock order
tools (no deployed agent, no Terraform). A "simplified" prompt 1.1.0 loses the 5,000-rupee refund limit. The gate
against the baseline fails, and CI would stop it.

**Status:** the script is checked up to the cost estimate, without a cloud account; its prompt edit is pinned to
the pack's prompt by a test. It has not been run against a live service yet. The first person to run it should note
anything that differs from the expected output below.

## Before recording

- A sandbox account or project (no customer data), credentials in the standard chain, and model access enabled
  (Bedrock model access for the model, or Vertex AI API on).
- Add `pricing:` to the profile if you want a dollar estimate (the script prints "unknown" otherwise); the run is
  capped by `BUDGET_USD` (default 5) on priced calls. Expect ~130 model calls per run, three runs in all.
- `pip install "genai-eval-framework[aws]"` or `[gcp]`.
- A rehearsal: `STEP=dry` stops after the estimate. Do one full run off camera; models vary, and the baseline must
  pass before the candidate's failure means anything.

## Run

```bash
CLOUD=aws AWS_REGION=ap-south-1 BEDROCK_MODEL_ID=<model or inference profile> examples/live-walkthrough/walkthrough.sh
CLOUD=gcp GCP_PROJECT=<project> GCP_LOCATION=asia-south1 VERTEX_MODEL=<model> examples/live-walkthrough/walkthrough.sh
```

## Shot list (about 5 minutes)

| Time | Show | Say |
|---|---|---|
| 0:00 | The console: the sandbox account, the model enabled | "A real model on <cloud>, a sandbox account, no customer data" |
| 0:30 | Steps 1–2: init, the cost estimate | "Start from a pack; see the cost before anything runs" |
| 1:15 | Step 3: the baseline run on the live model | "Today's prompt: outcomes, tool calls, critical cases" |
| 2:15 | Step 4: the one-line diff | "A tidy-up that drops the refund limit" |
| 2:45 | Step 5: the candidate fails, exit code non-zero | "Over-limit refunds go through; the gate blocks the release" |
| 3:30 | The HTML report: the failing case, the tool call with the amount | "Here is the refund it shouldn't have made" |
| 4:15 | Step 6: the live-tested record; the CloudWatch / Cloud Logging entries of the calls | "Evidence the adapter worked against the live service" |
| 4:45 | Close: the docs link | |

## Expected output

- Step 3 passes, or warns only on latency.
- Step 5 exits 1, with the critical-cases gate failing on the over-limit refund case(s) and task success lower than
  the baseline. If the live model escalates anyway on 1.1.0, the failure doesn't reproduce: say so on camera, or
  use the offline recording.

## After

- Commit `validation.yaml` with the record. `genai-evals adapters` shows the provider as *live tested*.
- Keep the account clean: the run creates no cloud resources; the model calls are logged only if invocation
  logging is on.
