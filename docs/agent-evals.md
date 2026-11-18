# Agent and safety evals: Anthropic's guidance, mapped

Two Anthropic articles were reviewed against v0.4.5 on 18 Nov 2026:
[Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) and
[Building strong safety evaluations](https://www.anthropic.com/research/building-strong-safety-evaluations).
The [webinar on evals for AI agents](https://claude.com/resources/webinars/evals-for-ai-agents-how-product-builders-get-the-most-out-of-every-new-model)
was checked too. Its public page only names three goals: evaluate agents end to end, build the first eval set from
production failures, and decide quickly whether a new model is worth switching to. All three were already covered:
agent targets with trajectories and final state, `cases harvest`, and model-upgrade experiments.

This page maps each recommendation to what the framework does. v0.5.0 added the rows marked **new**, and v0.5.1
the ones in [Added in v0.5.1](#added-in-v051). What's left is under [Not covered](#not-covered).

## Agent evals

| Recommendation | In the framework |
|---|---|
| Separate the task, trial, grader, transcript and outcome | Case, repeat, grader, `Sample.messages` / `trajectory`, `Sample.final_state` |
| Several trials per task; `pass@k` for "can it", `pass^k` for "will it every time" | `repeats`, `pass_at_k`, `pass_hat_k`, per-case pass rate across trials |
| Grade outcomes, not paths, unless the path matters | `final_state` for outcomes; `tool_sequence` checks order only (subsequence by default); packs gate on the outcome |
| Partial credit where the task has parts | Grader values run from 0 to 1 (`field_match` per field, rubric scales) |
| **Every task is solvable, with a reference that passes the graders** | **new:** `genai-evals cases check-refs suite.yaml` grades every case's reference as if it were the output. A known-good answer that fails a grader means a broken task or grader ([details](growing-the-eval-set.md#task-health)) |
| **Investigate 0% pass rates** | **new:** every result lists cases that never passed on any trial or target, and `genai-evals cases health result.json` shows them |
| **Watch for saturation** | **new:** graders that every regression case passes on every trial are reported as saturated: they can only catch regressions now |
| **Capability evals start low; regression evals stay near 100%; graduate one to the other** | **new:** cases carry `purpose: regression` (default, gated) or `capability` (reported, never gated). `cases graduate` lists capability cases that passed every trial in every given run, and `--apply` moves them to regression |
| **Isolate trials: each starts from a clean environment** | **new:** a per-trial `reset:` hook on any target (Python function or URL). Every trial gets a unique `trial_id` (HTTP body `{{ trial_id }}`, Python `ctx["trial"]`). A warning appears when pass rates move with trial order, a sign that trials share state. The tool loop, AgentCore and Bedrock Agents already started each trial fresh |
| **Balanced problem sets: test where a behaviour should and should not happen** | **new:** coverage counts both sides of refusal, escalation, tool use and handoff from the references, and flags one-sided behaviours. The packs gained paired cases: two no-tool cases (agent) and two sensitive-but-legitimate questions (RAG, `benign_sensitive` tag) |
| Calibrate model graders against human experts | `labels sheet`, multi-reviewer consensus, `calibrate` with κ intervals, sampling design and a validation sample ([human labels](human-labels.md)) |
| **Give judges an "unknown" option** | **new:** every rubric and policy judge may answer `"unknown"` instead of guessing. Unknown is neither pass nor fail: it is counted, left out of means, treated as not passing on critical cases, and queued for review |
| Structured rubrics, one isolated judge per dimension | One grader per dimension, each with its own judge calls; claim-level faithfulness |
| Make graders hard to game | Deterministic checks first; judges see the case input and output only, not tags, ids or criticality |
| Read the transcripts | The HTML report drills down per case: output, trajectory, final state, each judge's rationale |
| Multi-turn conversations with a simulated user | `goal` + `persona` with a pinned simulator profile, or scripted `turns` |
| Combine with production monitoring, A/B tests and user feedback | `genai-evals online` on production telemetry; harvested failures; experiments. User feedback capture is catalog item 6.4 |
| Eval-driven development; owners for the suite | Experiments; the pack handover checklist names owners |

## Safety evals

| Recommendation | In the framework |
|---|---|
| **In multi-turn evals, one bad turn fails the conversation** | **new:** `per_turn: true` on `rubric` and `policy` graders judges every assistant turn, with the earlier turns as context, and passes only if all pass. The grader's pass rate is the share of conversations that stayed acceptable throughout. The first failing turn is recorded |
| **Send items where the grader is unsure to human review** | **new:** `genai-evals labels queue result.json` writes a reviewer sheet of unknown verdicts, panel splits (judges disagreeing on pass / fail) and persistent judge errors. It is a *targeted* sample, kept out of calibration |
| Measure harmful compliance and over-refusal together | `refusal_appropriate` / `correct_refusal` grade both directions; **new** balance check and `benign_sensitive` tag |
| A risk-specific rubric written by domain experts, not a generic refusal metric | `policy` graders take the customer's own policy text; the labelling guide gives examples per level |
| Grader is a different model from the system under test | `judge_policy.require_different_family` |
| Control position and length bias | Pairwise position swap; the length rule and `length_bias` reporting |
| Compare grader–human agreement with human–human agreement | `calibrate` reports judge κ next to reviewers' Fleiss κ |
| Fail loudly when grading breaks | Judge coverage and failure rate; transient vs persistent errors; `regrade` (v0.4.4–0.4.5) |
| Hide the eval's design from the grader | Judges never see tags, criticality, splits or expected verdicts |
| Fix the success criteria before the run | Gates live in `suite.yaml` and are committed before the run; experiments record a hypothesis and a manifest |
| Report uncertainty for every estimate; use paired comparisons | Bootstrap CIs on every aggregate; paired bootstrap and McNemar; minimum-detectable-difference warnings |
| Correct for multiple comparisons | Bonferroni in experiments (not across gates: see below) |
| Report results by category | Slices by tag; representative vs stress splits |
| Document the full configuration | Judge provenance, config hashes, prompt versions, and the experiment manifest |

## Added in v0.5.1

| Recommendation | In the framework |
|---|---|
| Make criteria atomic and binary rather than one overall score; say what doesn't count | `checklist` grader: each criterion judged separately (met / not met / unknown), with `not_met` edge cases ([checklists](graders.md#checklists-atomic-passfail-criteria)) |
| Re-grade with a different model or rubric wording; if conclusions change, you measured the grader | `genai-evals sensitivity`: re-grades the same samples with reworded rubrics and reports flips, κ and changed gates ([details](graders.md#is-the-result-about-the-rubric)) |
| Enough items for the claim; intervals wider than about ±10 points may hide real differences | `genai-evals power` (interval width, detectable change, paired vs unpaired) and a run note when a gated grader's interval is wider than ±10 points ([how many cases](gates.md#how-many-cases)) |
| Vary scenario dimensions independently | `genai-evals cases grid`: full, sampled or pairwise designs, tagged by dimension ([scenario grids](growing-the-eval-set.md#scenario-grids)) |
| Correct for multiple comparisons | `gate_correction: holm` across every `significant()` in the run ([gates](gates.md#statistical-assumptions)) |

## Not covered

- **Cluster-aware intervals.** Cases from the same customer or document are treated as independent
  ([roadmap](roadmap.md)).
- **Contamination checks** (whether eval items appeared in a model's training data): not measurable from outside the
  model. Keep held-out cases private, and refresh them from production.
