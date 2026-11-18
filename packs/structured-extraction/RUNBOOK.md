# Runbook: Structured extraction

The path from a clean project to a customer-owned evaluation, for the `structured-extraction` pack (0.2.0).
Each step lists what to run and what "done" looks like. `genai-evals doctor` checks steps 1 and 4 to 9.

## 1. Run it offline (day 0, 10 minutes)

```bash
pip install "genai-eval-framework[openai,aws,gcp]"
genai-evals init my-eval --pack structured-extraction     # already done if you are reading this in your project
cd my-eval
genai-evals run suite.yaml --out out/offline --allow-missing-baseline
```

Done when `out/offline/report.html` opens and shows every gate. It uses the simulated system and mock judges:
nothing leaves your machine.

## 2. Agree what "good" means (workshop with the customer)

Walk the customer's product owner and SMEs through the sample report and decide:

- **Failure categories** that matter (the pack's defaults):
  - **invalid output**: not JSON, or JSON that breaks the output schema (valid_json)
  - **wrong or missing field**: a field differs from the reference after normalisation (fields)
  - **wrong amount or currency**: the payable total or its currency is wrong (amount_exact)
  - **too slow**: over the latency budget (latency)
- **Critical cases**: scenarios that must never fail, whatever the averages say.
- **Gate thresholds**: start from the pack's gates (below) and adjust to the customer's risk appetite.

Record the decisions in `HANDOVER.md` (owners) and `suite.yaml` (gates).

## 3. Build the dataset

Follow [`data/README.md`](data/README.md). Validate as you go:

```bash
genai-evals validate suite.yaml          # schema errors name the case id and the field
genai-evals cases coverage suite.yaml    # the representative set (size, provenance) and the stress suite (cases per tag)
genai-evals cases check-refs suite.yaml  # every reference must pass the graders: a rejected one is a broken task or grader
genai-evals cases day0 --docs <customer documents> --generator <strong model profile> --out-dir day0   # day 1, no dataset: ~80 drafts + review sheet for the SME
genai-evals cases sample out/online/result.json --n 200 --by-tag --out rep.jsonl   # representative set from production
genai-evals cases generate suite.yaml --generator <strong model profile>   # optional: draft cases for missing tags
```

Keep two sets ([two sets](https://github.com/koushikmohanbhat/genai-eval-framework/blob/main/docs/growing-the-eval-set.md#two-sets-representative-and-stress)):
a **representative** set sampled from production traffic (at least 30 cases, with `weight:` where the sample isn't in
production proportions, and a `representative_sample:` provenance record in suite.yaml), whose score is the production
estimate; and a **stress** suite of `edge` / `adversarial`
cases that find failures. The headline gates use `split='representative'`; the stress suite is gated separately.

Generated (and later, harvested) cases are drafts: they don't count until an SME writes the reference and
`genai-evals cases promote` marks them reviewed ([growing the eval set](https://github.com/koushikmohanbhat/genai-eval-framework/blob/main/docs/growing-the-eval-set.md)).

Done when the dataset has the agreed slices and critical cases, every applicable coverage tag has cases (or the gap
is agreed), SMEs have reviewed it, and personal data is tokenised.

## 4. Connect the customer's system

Pick the target file that matches their stack, fill its variables in `.env` (copy `.env.example`), and switch to live
providers:

| Target file | System |
|---|---|
| `targets/offline.yaml` | pattern-based extractor (offline) |
| `targets/prompt.yaml` | any model with the versioned prompt and the output schema |
| `targets/http.yaml` | any deployed extraction service or IDP pipeline |
| `targets/python.yaml` | your extraction code (e.g. wrapping Textract or Document AI) |

```bash
# .env
EVAL_TARGETS=targets/prompt.yaml
GENAI_EVALS_PROVIDERS=providers.live.yaml
```

```bash
genai-evals check-config providers.live.yaml
genai-evals run suite.yaml --out out/live-1 --allow-missing-baseline
```

Done when the run completes against the real system and the report's "Adapters used" table lists the live provider.
Keep only the profiles and targets the customer uses.

## 5. Calibrate the judges

This pack's graders are all deterministic, so there are no judges to calibrate. If you add a judge-graded
grader (rubric, policy, faithfulness), label at least 30 cases and run `genai-evals calibrate` before its gate counts.

## 6. Set the release gate and baseline

```bash
genai-evals run suite.yaml --out out/main --allow-missing-baseline
genai-evals baseline promote out/main/result.json --out baselines/main.json
```

Copy `ci/github-actions-evals.yml` into the customer's repository (or port the two commands to their CI: the exit code
is the gate). Done when a pull request runs the suite against `baselines/main.json` and a deliberate regression fails it.

Two tiers, same suite and gates: `--profile pr-check` on every pull request (one calibrated judge, one repeat, a
capped sample with every critical case) and `--profile release-gate` before production sign-off (the judge panel,
three repeats, every case). The CI template does both; gates a profile can't evaluate are listed as skipped.

Recommended gates for this pack:

| Gate | Rule | Why |
|---|---|---|
| valid-json | `mean(valid_json) >= 1.0` | downstream systems need every document to parse |
| field-accuracy | `mean(fields, split='representative') >= 0.95 and ci_low(fields, split='representative') >= 0.9` | the accuracy the business case assumed, on production-like documents |
| field-accuracy-under-stress (warn) | `mean(fields, split='stress') >= 0.85` | scans, informal and other edge documents, reported apart from the production estimate |
| amounts | `mean(amount_exact, split='representative') >= 0.98` | amounts go straight into payments |
| critical-documents | `critical_pass()` | high-value invoices and non-invoices never fail |
| ocr-slice (warn) | `mean(fields, tag='ocr') >= 0.9` | scanned documents are where extraction breaks first (raise min_n as OCR cases grow) |
| cost-budget (warn) | `mean(cost_usd) <= 0.01` | per-document cost from the business case |
| latency-budget (warn) | `p95(latency_ms) <= 5000` | set from the processing SLO |

## 7. Connect telemetry (production monitoring)

Keep the customer's source in `sources.yaml`, remove `fixture:`, and run:

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --out out/online
```

Add `--publish cloudwatch` or `--publish cloud_monitoring` to alarm on scores. Each month, turn the failures into new
cases: `genai-evals cases harvest out/online/result.json --out drafts.jsonl`, review, then `cases promote`. Done when a
scheduled run grades real sessions and an alert reaches the team that owns the system.

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
