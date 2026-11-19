# Graders and judges

Run deterministic graders first: they are free and repeatable. Use model graders for what code can't decide, and
human labels to calibrate the model graders.

Every grader accepts `threshold` (pass when value ≥ threshold), `tags` (grade only cases carrying one of these tags),
`output_path` (dotted path into the JSON output), `reference_key` / `value`, and `judges` (overriding the suite's panel).

| Family | Types |
|---|---|
| String / structure | `exact`, `contains` (any / all / negate), `regex`, `json_schema`, `field_match`, `numeric_tolerance` |
| Similarity | `rouge` (ROUGE-L), `bleu`, `embedding_similarity` (suite `embeddings` profile) |
| Trajectory | `tool_called`, `tool_not_called`, `tool_args_match`, `tool_sequence` (exact / subsequence / unordered), `final_state`, `step_budget`, `handoff_to`, `no_handoff_loop` (`max_handoffs`, `allow_return`) |
| RAG | `retrieval_recall_at_k`, `mrr`, `ndcg` (need relevance labels), `context_precision`, `context_recall` (judge-labelled, no relevance labels), `faithfulness`, `answer_relevance`, `citation_accuracy`, `correct_refusal` |
| Judges | `rubric` (1–5 or pass/fail, anchors), `reference_match`, `policy`, `pairwise` (`against: <target>`, both orders, length recorded, optional `max_length_ratio`) |
| Ops | `latency` (`max_ms`, `field`), `cost` (`max_usd`), `token_budget` |
| Safety / privacy | `pii_leak` (Aadhaar with Verhoeff check, PAN, UPI, +91 phones, email, Luhn-valid cards), `refusal_appropriate`, `guardrail_triggered` |
| Custom | `python` (`module:fn(sample, case, params)`), `http`, `agentcore_evaluator` (experimental), `vertex_metric` (experimental) |

### Start here

Each pack marks the few graders to read first (`start_here` in `pack.yaml`; `genai-evals pack info <pack>` lists
them, and the rest as advanced; `init` prints them). The `quickstart` profile runs a subset of them, so there is one
starter list per pack, and a test keeps it that way. Start with these, and add an advanced one when a failure you care
about is invisible to them. One shared list across packs would not work: `faithfulness` means nothing for extraction,
`final_state` nothing for RAG.

| Pack | Start here | Advanced |
|---|---|---|
| rag-knowledge-assistant | `faithfulness`, `relevance`, `citations`, `refusal`, `recall_at_4`, `no_pii`, `latency` | `mrr`, `context_precision`, `context_recall`, `safe_content` |
| tool-agent | `task_success`, `correct_calls`, `lookup_first`, `no_refund_over_limit`, `escalated`, `no_pii`, `latency` | `no_refund_undelivered`, `no_address_change_after_ship`, `steps`, `no_handoff_loop`, `no_promises` |
| structured-extraction | `valid_json`, `fields`, `amount_exact`, `latency` | none |

`field_match` normalisers: `text`, `name` (order-insensitive), `date` (many Indian and ISO formats), `amount`
("₹1.2 lakh", "45k", "2 crore"), `phone`, `digits`, `upper_alnum`, `exact`. Lists are scored with F1.

## Judge panel rules

- **No default judge.** A suite with model graders must name `judges:`, and validation fails otherwise. Use two
  providers (for example `judge-openai` and `judge-gemini`).
- Calls use temperature 0 and JSON verdicts. Each judge's value and reasoning is stored, along with the panel's
  `disagreement`.
- **A short, checkable rationale before the verdict.** Every verdict schema puts `rationale` (or per-claim /
  per-chunk `evidence`) before the score, winner or verdict. Judges are asked for at most two sentences that quote
  the deciding words from the output, reference or context (claim evidence: a quote of at most 20 words, or "none").
  They are told not to write out step-by-step thinking, and not to repeat personal details. The point is a reason a
  reviewer can check against the texts, not a private chain of thought.
- **Stored rationales are minimal.** Each judge's rationale is whitespace-collapsed and capped at
  `judge_policy.max_rationale_chars` (default 300). `judge_policy.store_rationale: false` keeps only the verdicts.
  Rationales and evidence are stored in `reasoning` and `details`, which go through the run's tokeniser exactly like
  outputs ([DPDP](dpdp.md#data-flow)). Judges only ever see tokenised inputs, so they can't echo raw identifiers.
- **Length is not quality.** Rubric, relevance and pairwise prompts tell the judge not to reward length, padding or
  repetition. `pairwise` also records both answers' word counts on every score, and the run summary reports
  `length_bias`: `longer_win_rate` (share of decided comparisons won by the longer answer), the mean length ratio,
  how many comparisons were `length_confounded` (ratio above `max_length_ratio`), and position consistency. A
  longer-answer win rate far above 50% when lengths differ a lot means the judge is rewarding length.
- **"unknown" instead of a guess.** Rubric and policy judges may answer `"unknown"` when the texts given don't let
  them decide. If every judge that answered says unknown, the score is neither pass nor fail. It is recorded as a
  skip labelled `unknown`, left out of means, counted per grader (`unknown`), and listed by `labels queue` for a
  person to decide. On a critical case it counts as not passing. If one judge of a panel says unknown, the others
  decide (`unknown_judges` records who abstained).
- **Split verdicts.** When the panel's judges land on different sides of the threshold, the score records
  `split_verdict: true`; `labels queue` picks these up.
- `judge_policy.require_different_family: true` drops judges from the same model family as the target, to avoid
  self-preference.
- `judge_policy.aggregate`: `mean` (default), `min` or `majority`.
- Judge inputs are tokenised (DPDP) before they leave the process.

## Grading every turn of a conversation

For multi-turn agents, judging only the final answer misses a harmful or off-policy turn in the middle. With
`per_turn: true`, a `rubric` or `policy` grader judges **every assistant turn**, with the earlier turns as context,
and the conversation passes only if every turn passes:

```yaml
- {name: policy, type: policy, policy: "Never promise delivery dates. Never share another customer's data.",
   scale: pass_fail, per_turn: true}
```

- One bad turn fails the conversation, whatever came after. The grader's pass rate is the **share of conversations
  that stayed acceptable throughout**.
- `details.turns` has each turn's verdict and rationale; `details.first_failing_turn` says where it went wrong.
  `value` is the lowest turn's value.
- A turn the judges couldn't decide makes the conversation `unknown` (unless another turn failed). A judge error
  on any turn makes it an error, retryable with `regrade`.
- It costs one judge call per assistant turn per judge, and by default **every** turn is graded, however long the
  conversation. Single-turn samples are judged once, as before.
- `max_turns` caps the cost, but a conversation over the cap is never silently passed. `over_max_turns` decides:
  `unknown` (default: not graded, counted as unknown, not a pass on critical cases, and listed by `labels queue`),
  `fail`, or `grade_last` (grades the last `max_turns` turns and records `details.truncated` with the original numbers
  of the turns that were and weren't graded). Turn numbers in `details.turns` are always the conversation's own.

## Checklists: atomic pass/fail criteria

One overall 1–5 score hides *why* an output fell short, and judges disagree most on overall scores. A `checklist`
grader splits the rubric into criteria that are each met or not met, judged **one at a time** by separate judge
calls, so one criterion can't colour another:

```yaml
- name: claim_reply
  type: checklist
  use_reference: true
  criteria:
    - "States which documents the customer still needs to send"
    - {id: no_promise, text: "Makes no promise that the claim will be paid",
       not_met: "Phrases like 'you will be covered' or 'this should be approved' count as promises, even with a caveat."}
    - {id: next_step, text: "Gives one clear next step", not_met: "A list of options without a recommendation does not count."}
```

- `not_met` spells out what does **not** count, including the edge cases (a disclaimer instead of the content,
  pseudocode instead of code, a partial answer). It is shown to the judge as `<does_not_count>`.
- `require_all: true` (default): the output passes only if every criterion is met. `value` is the share met.
  `require_all: false`: passes when the share met reaches `threshold` (default 0.75).
- Each criterion can be met, not met or `unknown`. With `require_all`, one unmet criterion fails the output even
  if others are unknown; otherwise an unknown makes the whole score `unknown`.
- `details.criteria` holds each criterion's verdict and rationale; `details.not_met` lists the ids that failed, so
  reports can say *which* criterion fails most.
- Cost: one judge call per criterion per judge.

## Is the result about the rubric?

If rewording a rubric changes the verdicts, the eval measured the grader, not the system. `sensitivity` re-grades
the same samples (the system is not re-run) with reworded rubrics, and compares:

```bash
genai-evals sensitivity out/run/result.json --suite suite.yaml --grader no_payout_promise \
  --variant 'Never promise a payout amount or say "we will pay".' \
  --variant 'Do not say "will be paid" or "covered".' --baseline baseline.json
#   variant-1: 0/48 verdicts flipped (0%: 0 pass->fail, 0 fail->pass), kappa vs original 1.00, mean 1.00 -> 1.00
#   variant-2: 0/48 verdicts flipped (0%: ...), kappa vs original 1.00, mean 1.00 -> 1.00
#   robust: rewording didn't change the conclusion
```

- A one-line `--variant` replaces the grader's `rubric` (or `policy`). `--variants file.yaml` takes a list of
  parameter dicts for anything else: `{name: tighter, rubric: ..., anchors: {...}}`, or new `criteria` for a checklist.
- For each variant it reports the verdicts that flipped (pass→fail and fail→pass), Cohen's κ with the original
  verdicts, the change in the mean, and **any gate whose outcome changed**.
- **Robust** means no gate changed and at most `--max-flip-rate` (default 10%) of verdicts flipped; the command
  exits 1 otherwise. Without `--baseline`, `delta()` gates can't be decided on the re-graded result, so they are
  left out of the comparison and listed.
- Cost: every unit is re-graded once per variant. Use it on the judge behind a release gate, when a rubric
  changes, and before trusting a new judge.

## Security graders

`prompt_leak` (system prompt leakage: a canary, or 8+ words copied from the prompt file) and `injection_resisted`
(the system didn't follow an instruction planted in a document or tool result) are deterministic. They're used by the
security-guardrails overlays: see [security](security.md).

## RAG context graders

- `context_precision`: the judge marks each retrieved chunk useful or not for answering the question (using the
  reference answer when the case has one). The score is average precision, so useful chunks ranked low cost more.
  `k` (default 10) limits the chunks judged. Default threshold 0.7.
- `context_recall`: the judge splits the reference answer into statements and checks which ones the retrieved
  context supports. Skips cases with no reference answer. Default threshold 0.85.

Use them when the dataset has no per-document relevance labels; `retrieval_recall_at_k`, `mrr` and `ndcg` are
cheaper and exact when it does.

## Calibration

```bash
genai-evals calibrate out/result.json --grader faithfulness --labels labels.csv --out calibration.json --min-kappa 0.4
```

The labels CSV has columns `case_id,target,label`, where `target` may be `*`. With several reviewers, add `reviewer`
(and optionally `round` and `adjudicated`): labels are combined into a consensus first and the reviewers' agreement
is reported alongside. `--compare-to <grader>` checks whether a cheaper judge can replace a stronger one. Both are
covered in [human labels and the judge ladder](human-labels.md). Label sheets record their sampling design and
weights, so calibration reports production-weighted agreement and agreement per slice; `--validation-labels` adds a
separate representative sample, and a trusted judge must clear `min_judge_kappa` on both. Point the suite at the file with
`calibration: calibration.json` and set `min_judge_kappa: 0.4`.

A judge is **trusted** only when it was calibrated on at least `min_calibration_n` labels (default 30) **and** the lower
bound of the bootstrap 95% CI on κ is at or above `min_judge_kappa`. A point estimate above the threshold is not
enough: κ 0.63 on 40 labels has a CI of roughly 0.3–0.9. Any gate that depends on an untrusted grader is reported
**invalid**, and fails if it is blocking. `genai-evals calibrate` exits 1 for an untrusted grader.

The calibration file also reports the human pass rate, positive and negative agreement and balanced accuracy, with
warnings when labels are too few or when the human pass rate is below 10% or above 90% (κ is unstable at extreme
prevalence; check positive and negative agreement separately).

## Judge provenance

Each result records, under `judge_provenance`: a hash of the judge prompt templates, temperature, response format,
the judge policy, and per judge the provider, configured model, model family, region, options and the model ids the
provider actually reported. `graders` records each grader's type and a hash of its configuration. A judge-graded
number from a different prompt template or judge model is not comparable; check these before comparing runs.
