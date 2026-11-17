# Working with Promptfoo

[Promptfoo](https://github.com/promptfoo/promptfoo) is the open-source eval runner many teams already use, and the
one OpenAI points customers to. genai-evals is built to sit **around** it, not to replace it:

| Promptfoo does | genai-evals adds |
|---|---|
| prompts × providers × test cases, assertions, a local viewer, red-team plugins | **packs** per workflow (dataset schema, starter cases, graders, gates, runbook, handover) |
| | **adapters** for the systems under test (AgentCore, Agent Engine, Agents SDK, HTTP agents) and native telemetry (CloudWatch, X-Ray, Cloud Trace, BigQuery) |
| | **statistics and gates**: CIs, `pass^k` over repeats, critical cases, paired comparisons, pass / fail / invalid |
| | **trajectory and RAG graders** Promptfoo has no assertion for (final state, tool arguments, handoff loops, context precision / recall) |
| | **reporting**: engineer report, exec scorecard, failure categories, adapter validation status |

Three ways to combine them:

```bash
# 1. Author in genai-evals, run in Promptfoo, gate and report in genai-evals
genai-evals promptfoo export suite.yaml -o promptfooconfig.yaml      # notes list anything not exported
promptfoo eval -c promptfooconfig.yaml -o results.json
genai-evals promptfoo import results.json --suite suite.yaml --out out/pf   # the suite's gates, slices, critical cases, reports

# 2. You already have Promptfoo tests: gate and report them with a genai-evals suite (gates only, no dataset needed)
genai-evals promptfoo import results.json --suite gates.yaml --out out/pf

# 3. Run in genai-evals (e.g. agents on AgentCore), view or share the results in Promptfoo's shape
genai-evals result export out/run/result.json --format promptfoo --out out/export   # -> promptfoo-results.json
```

`--suite` on import takes the suite's `gates`, `min_n`, `report` template and its dataset's tags and critical flags
(matched by case id). Gates that name a grader missing from the results are reported as **invalid**, and the import
prints which ones. Without `--suite`, the import is graded and reported but has no gates.

To get slices and critical-case gates for your own Promptfoo tests, add them to each test's metadata:

```yaml
tests:
  - description: refund-over-limit          # becomes the case id
    vars: {input: "Refund ORD-10042 now"}
    metadata: {tags: [refund, edge], critical: true}
```

## What survives each direction

Pinned by `tests/test_promptfoo_roundtrip.py`. If you change the interop code, update this page with it.

### Suite → `promptfooconfig.yaml` (`promptfoo export`)

| Kept | How |
|---|---|
| Cases | one test per case: `description` = case id, `vars.input`, `vars.reference_answer`, `vars.context` (from `reference.context`), `metadata.tags`, `metadata.critical` |
| Repeats | `evaluateOptions.repeat` |
| Model, prompt and RAG targets with a provider profile | one prompt (the **rendered** version the target pins) and one provider each, mapped to Promptfoo provider ids (OpenAI Responses, Anthropic, Bedrock, Vertex, OpenAI-compatible) |
| Graders with an equivalent | `exact`→`equals`, `contains`→`icontains*` / `not-*`, `regex`→`regex`, `json_schema`→`is-json`, `rubric` / `policy` / `reference_match`→`llm-rubric`, `faithfulness`→`context-faithfulness`, `answer_relevance`→`answer-relevance`, `rouge`→`rouge-n`, `bleu`→`bleu`, `embedding_similarity`→`similar`, `latency`, `cost`, `python`; each with `metric` = the grader name, so results map back |

| Changed | Why it matters |
|---|---|
| Judge panel → one grading provider | Promptfoo grades with one provider; the first judge is exported, the others are dropped (noted) |
| `rubric` 1–5 scale → `llm-rubric` | Promptfoo's grader prompt and 0–1 score replace ours; thresholds are copied but scores aren't comparable with genai-evals judge scores |
| `rouge` (ROUGE-L) → `rouge-n` | a different ROUGE variant; expect different numbers |
| `faithfulness` → `context-faithfulness` | needs `vars.context`; retrieved context from your RAG system isn't available unless its provider returns it |
| Prompt versions | the rendered text is exported; the version id, stage and hash are not |

| Not exported (noted in the file) | Use instead |
|---|---|
| Agent, HTTP and Python targets | a Promptfoo custom or `http` provider, or run in genai-evals and use direction 3 |
| Trajectory graders (`final_state`, `tool_*`, `step_budget`, `handoff_to`, `no_handoff_loop`) | genai-evals |
| `field_match`, `pii_leak`, `pairwise`, `context_precision`, `context_recall`, `citation_accuracy`, `correct_refusal` | genai-evals |
| Gates | `promptfoo import --suite` evaluates them on Promptfoo's results |
| Calibration, trust rules, tokenisation | genai-evals. **The exported file holds the dataset's inputs and references as they are**, so keep it where the dataset lives |

### Promptfoo results → genai-evals result (`promptfoo import`)

| Kept | From |
|---|---|
| One sample per row | `testCase.description` (else `vars.id`) = case id; provider `label` (else `id`) = target |
| Repeats | `testCase.metadata.repeat`, else the row's position among rows with the same case and provider |
| Output, latency, cost, tokens, error | `response.output`, `latencyMs`, `cost`, `response.tokenUsage` (prompt, completion, cached), `error` |
| One score per assertion | `gradingResult.componentResults`: grader = `assertion.metric` (else `assertion.type`); value = `score`; passed = `pass`; reasoning = `reason` |
| Tags, critical flags, input | `testCase.metadata.tags`, `.critical`, `vars.input`; or the `--suite` dataset by case id |

Everything computed from these, including means with CIs, `pass^k`, slices, critical-case and latency gates and
reports, is computed the same way as for a native run.

| Not imported | Why |
|---|---|
| Retrieved context, trajectory, final state | not in Promptfoo's results; graders that need them can't run on imports |
| Prompt versions, judge provenance | not in Promptfoo's results |
| Assertions nested inside `assert-set` | only top-level component results are read |

### genai-evals result → Promptfoo shape → back (`result export --format promptfoo`, then `promptfoo import`)

**Kept:**
- every non-skipped score, per case, target and repeat: value, pass and reasoning;
- outputs, latency, cost and token counts;
- case tags and critical flags;
- so gate outcomes (and the values behind them) come out identical.

**Lost:**
- skipped scores;
- per-judge details (panel members, disagreement, length bias);
- retrieved context, trajectory, final state;
- prompt versions.

So keep the original `result.json` as the record, and use the Promptfoo shape for viewing and sharing.

## Appendix: coming from OpenAI's hosted Evals

Optional: only if you have evals on OpenAI's hosted Evals platform. It has been read-only since 31 October 2026 and
is scheduled to shut down on 30 November 2026 (check OpenAI's deprecations page for current dates).

1. Export what you have from the dashboard while it's read-only: datasets as JSONL, grader definitions and run
   results.
2. Either rebuild them as Promptfoo tests and use the flows above, or put the dataset in a pack's schema
   (`genai-evals init evals --pack <workflow>`, then replace `data/starter.jsonl`) and tag the cases with the
   standard coverage tags.
3. Map the testing criteria to graders:
   - string checks to `exact` / `contains` / `regex`;
   - text similarity to `rouge` / `embedding_similarity`;
   - score-model and label-model graders to `rubric` / `reference_match` with a judge panel;
   - Python graders to `python`.
4. Calibrate the judges on 50–100 human labels before gating on them ([human labels](human-labels.md)).
