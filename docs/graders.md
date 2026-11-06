# Graders and judges

Run deterministic graders first: they are free and repeatable. Use model graders for what code can't decide, and
human labels to calibrate the model graders.

Every grader accepts `threshold` (pass when value ≥ threshold), `tags` (grade only cases carrying one of these tags),
`output_path` (dotted path into the JSON output), `reference_key` / `value`, and `judges` (overriding the suite's panel).

| Family | Types |
|---|---|
| String / structure | `exact`, `contains` (any / all / negate), `regex`, `json_schema`, `field_match`, `numeric_tolerance` |
| Similarity | `rouge` (ROUGE-L), `bleu`, `embedding_similarity` (suite `embeddings` profile) |
| Trajectory | `tool_called`, `tool_not_called`, `tool_args_match`, `tool_sequence` (exact / subsequence / unordered), `final_state`, `step_budget`, `handoff_to` |
| RAG | `retrieval_recall_at_k`, `mrr`, `ndcg`, `faithfulness`, `answer_relevance`, `citation_accuracy`, `correct_refusal` |
| Judges | `rubric` (1–5 or pass/fail, anchors), `reference_match`, `policy`, `pairwise` (`against: <target>`, both orders) |
| Ops | `latency` (`max_ms`, `field`), `cost` (`max_usd`), `token_budget` |
| Safety / privacy | `pii_leak` (Aadhaar with Verhoeff check, PAN, UPI, +91 phones, email, Luhn-valid cards), `refusal_appropriate`, `guardrail_triggered` |
| Custom | `python` (`module:fn(sample, case, params)`), `http`, `agentcore_evaluator` (experimental), `vertex_metric` (experimental) |

`field_match` normalisers: `text`, `name` (order-insensitive), `date` (many Indian and ISO formats), `amount`
("₹1.2 lakh", "45k", "2 crore"), `phone`, `digits`, `upper_alnum`, `exact`. Lists are scored with F1.

## Judge panel rules

- **No default judge.** A suite with model graders must name `judges:`, and validation fails otherwise. Use two
  providers (for example `judge-openai` and `judge-gemini`).
- Calls use temperature 0 and JSON verdicts. Each judge's value and reasoning is stored, along with the panel's
  `disagreement`.
- `judge_policy.require_different_family: true` drops judges from the same model family as the target, to avoid
  self-preference.
- `judge_policy.aggregate`: `mean` (default), `min` or `majority`.
- Judge inputs are tokenised (DPDP) before they leave the process.

## Calibration

```bash
genai-evals calibrate out/result.json --grader faithfulness --labels labels.csv --out calibration.json --min-kappa 0.4
```

The labels CSV has columns `case_id,target,label`, where `target` may be `*`. Point the suite at the file with
`calibration: calibration.json` and set `min_judge_kappa: 0.4`. Any gate that depends on a grader below that κ is
reported **invalid**, and fails if it is blocking.
