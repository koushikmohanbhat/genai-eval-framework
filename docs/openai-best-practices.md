# Mapping to OpenAI's evaluation best practices

OpenAI's [evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) guide
describes how to design evals for production systems. This page maps each recommendation to what the framework does,
so a partner can show a customer, section by section, that their evals follow it. Where the framework doesn't yet
cover something, it says so.

Promptfoo is the main way this framework works with other eval tooling: see [Working with Promptfoo](promptfoo.md).

## The process

| Guide | Framework |
|---|---|
| **Define the objective**: success criteria first | `suite.yaml` `gates:` are the success criteria, written as rules (`mean(faithfulness) >= 0.85 and ci_low(faithfulness) >= 0.7`), each pack's gates come with the reason for each ([gates](gates.md)) |
| **Collect the dataset**: production, expert, historical, synthetic | dataset files with a schema per pack; tokenised production samples via `cases harvest`; synthetic drafts via `cases generate`; expert review before either counts ([growing the eval set](growing-the-eval-set.md)) |
| **Define metrics** | 40 graders: deterministic, trajectory, RAG and judge-based ([graders](graders.md)) |
| **Run and compare** | `genai-evals run`, paired comparisons with a baseline, and experiments (a baseline plus candidate changes on the same cases, with corrected significance) ([experiments](experiments.md)) |
| **Evaluate continuously** | CI gates on every change (GitHub Action, CodeBuild, Cloud Build), online evals on production telemetry, and harvesting failures back into the eval set ([CI](ci.md), [sources](sources.md)) |

## Datasets

| Guide | Framework |
|---|---|
| Mix production data, expert references and historical logs | `source: production | sme | synthetic` on every case; online runs build cases from production samples; `cases harvest` turns failures into drafts |
| Include typical, edge and adversarial cases | standard difficulty tags. `edge` / `adversarial` cases form a **stress suite**, reported and gated apart from the **representative** set (production-like, optionally prevalence-weighted), so stress cases never distort the production estimate. The representative set needs evidence: a provenance record and mostly production cases (`cases sample` draws it from production telemetry). Stress tags need a minimum number of cases by risk tier. `doctor` checks all of this and flags gates that average the sets or that will lack cases |
| Use a strong model to generate scenarios, then have experts label them | `cases generate --generator <strong model profile>` writes **draft** cases per coverage tag; drafts are left out of runs and gates until `cases promote` marks them reviewed |
| Prefer pairwise, classification or criterion-based tasks over open-ended scoring | `pairwise` (both orders), pass/fail rubrics, `reference_match`, `policy`; 1–5 rubrics support anchors per level |

## Graders

| Guide | Framework |
|---|---|
| Metric-based evals for regression tests (exact match, ROUGE/BLEU, function-call accuracy, executable tests) | `exact`, `contains`, `regex`, `json_schema`, `field_match`, `rouge`, `bleu`, `tool_called`, `tool_args_match`, `tool_sequence`, `final_state`, `python` / `http` for executable checks |
| Human evals: several rounds, examples at each level, pass/fail thresholds, consensus | `labels guide` (examples per level), `labels sheet` (independent rows per reviewer, no judge verdicts shown), `labels agreement` (Fleiss' κ, per-reviewer agreement, disputed items), majority / unanimous / adjudicated consensus ([human labels](human-labels.md)) |
| Model graders: pairwise, single-answer and reference-guided | `pairwise`, `rubric`, `reference_match`, `faithfulness`, `answer_relevance`, `context_precision`, `context_recall`, `policy` |
| Watch for position and verbosity bias | `pairwise` runs both orders and reports position consistency; every pairwise score records both answers' lengths, and the summary reports how often the longer answer won (`length_bias.longer_win_rate`), with optional `max_length_ratio` to flag length-confounded comparisons |
| Control for length | every judge prompt tells the judge that length is not quality and not to reward padding |
| Require reasoning before scoring | every verdict starts with a short, checkable rationale: at most two sentences quoting the deciding evidence, or per-claim quotes of at most 20 words. Judges are told not to write out step-by-step thinking or repeat personal details. Stored rationales are capped (300 characters by default), can be turned off (`judge_policy.store_rationale: false`), and are tokenised like the answers |
| Clear, detailed rubrics | rubric text plus `anchors` per level; the same text goes into the labelling guide |

## LLM-as-judge and calibration

| Guide | Framework |
|---|---|
| Start with a strong judge and check agreement with human labels | `calibrate` reports accuracy, Cohen's κ with a bootstrap CI, positive and negative agreement, and warns on small or imbalanced label sets; gates that depend on an untrusted judge are **invalid** |
| Move to cheaper judges only once they match human annotations | `calibrate --grader <cheap> --compare-to <strong>`: approves the cheaper judge only if the 95% lower bound of κ(cheap) − κ(strong) is above −margin (default 0.05) on the same labelled units, and reports both judges' cost. Each judge's coverage and failure rate are reported, units a judge failed to grade count as disagreements in a worst-case check, and the cheaper judge may fail on at most 5% of units. The comparison is design-aware: with a random `--validation-labels` sample, approval is decided there; otherwise on the calibration sample with production weights and a stratified bootstrap ([judge ladder](human-labels.md#the-judge-ladder)) |
| Expert human labels are the ground truth | consensus labels from several reviewers; the calibration warns when reviewers agree only moderately (κ < 0.6), because a judge can't be held to a standard the humans don't meet |
| (sampling for labels) | one label per (case, target), compared with the judge's verdict on the labelled repeat. The stratified calibration sample measures agreement on both outcomes and per slice, and records its design and inverse-inclusion weights. So calibration also reports production-weighted agreement, with stratified-bootstrap intervals. A separate random **validation** sample gives agreement on representative traffic, and a trusted judge must clear the bar on both |
| Judge quality varies by context | calibrate per grader and per suite; judge prompt hashes and model ids are recorded with every result, so a judge change is visible |

## Architectures

| Guide | Framework |
|---|---|
| **Single-turn**: instruction following, including system prompts overriding user prompts; functional correctness | `rubric`, `reference_match`, `policy`, `json_schema`, `field_match`; `system_conflict` coverage tag, with starter cases in the RAG and agent packs |
| **Workflows**: evaluate each step in isolation and the final output | evaluate each step as its own target (prompt + model), and the whole workflow as a `python` / `http` target with `final_state` and output graders |
| **Single agents**: tool selection and data precision (arguments from the conversation) | `tool_called`, `tool_not_called`, `tool_args_match`, `tool_sequence`, `final_state`; `bad_tool_args` coverage tag |
| **Multi-agent**: handoff accuracy, per-agent tool selection and arguments | `handoff_to`, `no_handoff_loop` (circular handoffs and handoff budget), the trajectory graders per agent |
| Only adopt multi-agent designs when evals justify the complexity | the [single vs multi-agent experiment](experiments.md#single-agent-vs-multi-agent) compares them on the same cases, with cost and latency alongside quality |

## Edge cases

The standard coverage tags (`genai-evals cases tags`) are the guide's list. `genai-evals cases coverage suite.yaml`
and `doctor` report which ones a dataset has no cases for, for the workflow it evaluates.

| Guide | Tags |
|---|---|
| Input variability | `multilingual`, `structured_input`, `image_input`, `noisy_input` (extraction) |
| Contextual complexity | `multi_intent`, `typos`, `very_short`, `long_conversation`, `ambiguous_tool_output`, `bad_tool_args`, `circular_handoff`, `no_answer` (RAG) |
| Personalisation | `jailbreak`, `system_conflict`, `format_request` |

Results are broken down by tag (`summary.slices`), so a pass rate on `multilingual` cases is reported separately from
the overall mean.

## Anti-patterns

| Guide warns against | Framework |
|---|---|
| Generic metrics only (perplexity, BLEU) | packs use task metrics first (final state, field match, faithfulness, citations); ROUGE/BLEU are available but no pack gates on them |
| Datasets that don't reflect production | `cases harvest` from online runs; doctor's coverage check; online evals on real traffic |
| Vibe-based evals; evaluating only after launch | gates in CI from day 0; packs run offline before any system is connected |
| Ignoring human feedback; uncalibrated judges | judges must be calibrated to be trusted, and untrusted judges invalidate gates |

## Continuous evaluation

| Guide | Framework |
|---|---|
| Run evals on every change | CI templates in every pack; `prompts promote` is gated |
| Monitor production for new nondeterminism | `genai-evals online` on native telemetry, `repeats` and `pass^k` offline |
| Keep growing the eval set | `cases harvest` → review → `cases promote`; the managed eval ops offer in the [partner playbook](partner-playbook.md) |
| Log everything during development | OTel spans for every eval run, linked to the original traces; results keep every sample, score and judge reasoning |
| Use the evals to drive improvements (fine-tuning) | experiments gate prompt and model changes; gating for fine-tunes is on the [roadmap](roadmap.md) |

## Not covered yet

- Voice / realtime evals (roadmap).
- Gating fine-tuning and distillation runs (roadmap, shared with catalog 7.2).
- G-Eval and BERTScore as named metrics: the rubric judges and `embedding_similarity` cover the same ground.

## Appendix: moving off OpenAI's hosted Evals

Optional, for customers with evals on the hosted Evals platform (read-only since 31 October 2026, scheduled to
shut down on 30 November 2026). The steps are in the [Promptfoo guide's appendix](promptfoo.md#appendix-coming-from-openais-hosted-evals).
