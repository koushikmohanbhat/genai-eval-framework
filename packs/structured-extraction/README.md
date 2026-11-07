# Eval pack: structured extraction

Evaluates a system that turns documents into JSON, whatever produces it: a model with a prompt, an IDP pipeline
(Textract, Document AI, a document-intelligence service) or your own code behind an API. The starter is supplier
invoices; change the output schema, reference fields and cases for other documents.

| | |
|---|---|
| **Measures** | schema validity, field-level accuracy with normalisers, payable total and currency, rejection of non-invoices, cost and latency per document |
| **You supply** | documents as text with expected fields ([how](data/README.md)), the output schema, and one target file |
| **Gates** | valid-json, field-accuracy (with CI), amounts, critical-documents; OCR slice, cost and latency as warnings ([why](pack.yaml)) |
| **Report** | failure categories: invalid output, wrong or missing field, wrong amount or currency, too slow |
| **Overlay** | `india-gst`: GST tax invoices, GSTIN format check, lakh / crore amounts |
| **Status** | runs offline in CI; no adapter is live tested yet ([adapters](../../docs/adapters.md)) |

## Quick start

```bash
genai-evals init my-extraction-eval --pack structured-extraction --overlay india-gst
cd my-extraction-eval                       # init has already run it offline: open out/offline/report.html
genai-evals experiment run experiment.yaml --out out/experiment
```

## Connect your system

| File | Use when | Set in `.env` |
|---|---|---|
| `targets/prompt.yaml` | a model does the extraction with the versioned prompt | `EVAL_TARGET_PROFILE` |
| `targets/http.yaml` | the extraction service is deployed behind an API | `SYSTEM_URL`, `SYSTEM_TOKEN`, `SYSTEM_OUTPUT_FIELD` |
| `targets/python.yaml` | your extraction code can be imported | `EXTRACTOR` |

No judges are needed: every grader is deterministic, so results are exactly repeatable and cost nothing to grade.

## Connect telemetry

Keep the block for the customer's stack in `sources.yaml` and remove `fixture:`; `online_checks.yaml` checks schema
validity, personal data and latency on production extractions (no reference values are needed).

## Files

`suite.yaml` · `schema/case.schema.json` and `schema/output.schema.json` · `data/starter.jsonl` · `targets/` ·
`prompts/invoice_extractor` · `providers.yaml` / `providers.live.yaml` · `sources.yaml` + `online_checks.yaml` ·
`experiment.yaml` · `overlays/india-gst` · `RUNBOOK.md` · `HANDOVER.md` · `validation.yaml` · `sim_system.py`.
