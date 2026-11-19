# Promptfoo first: layering governance on an existing suite

For partner architects whose customer already runs Promptfoo. The rule: **don't rip and replace.** Keep the
customer's tests, runner and viewer; add release gates, statistics, slices, DPDP handling and the reports a business
owner signs off. Mechanics and exact round-trip semantics are in [Working with Promptfoo](promptfoo.md).

## Week 1: gate what they already run

1. Run their suite as usual and keep the results file:
   `promptfoo eval -c promptfooconfig.yaml -o results.json`
2. Write a small `gates.yaml`: the gates, `min_n` and report template, and optionally a dataset whose case ids
   match their test descriptions (for tags and critical flags). Start from a pack's `suite.yaml`.
3. `genai-evals promptfoo import results.json --suite gates.yaml --out out/pf`: pass / fail / invalid gates,
   intervals, slices, the scorecard. Gates that name an assertion Promptfoo didn't run are **invalid** and listed.
4. Put that command in their CI after `promptfoo eval`. Nothing about their tests changed.

## Week 2: what Promptfoo doesn't cover

- **Agents and telemetry** stay on the genai-evals engine: tool trajectories, final state, per-turn grading, trial
  resets, online checks on production traces. Run those as separate suites; the reports look the same.
- **Judges they can trust:** calibrate Promptfoo's `llm-rubric` graders the same way, against human labels
  ([human labels](human-labels.md)). Until then, gates on them are a judge's opinion.
- **One command, if they prefer:** `genai-evals run suite.yaml --engine promptfoo` exports a genai-evals suite,
  runs Promptfoo and gates the results. Their existing `promptfooconfig.yaml` doesn't need converting to use step 3.

## Data handling: what "DPDP masking on top" does and doesn't cover

genai-evals tokenises structured identifiers in everything **it** writes: results, reports, exports, the PR comment,
using the suite's detector sets (`pii:`). It cannot reach back into Promptfoo:

| Where the data is | Masked by the import? |
|---|---|
| genai-evals results, reports, exports | yes |
| Promptfoo's `results.json` and its local database (`~/.promptfoo`) | **no**: raw |
| Prompts and outputs Promptfoo sent to its own grading models (`llm-rubric` and similar) | **no**: sent raw |
| The model under test | raw by design: the system must see real input |

So, for production-derived data:

- Tokenise the test inputs before Promptfoo sees them (`genai-evals cases harvest` / `cases sample` write tokenised
  cases; or use their own redaction step), or use synthetic and seeded cases only.
- Point Promptfoo's grading provider at an in-region model the customer has approved.
- Treat `results.json` and `~/.promptfoo` as raw personal data: keep them with the dataset, not in CI artifacts, and
  delete them on the customer's schedule.
- Tell the customer this in the SOW's data-handling section ([SOW template](sow-template.md)).

## Talking points

- "Your tests stay; we add the release decision around them."
- "We'll tell you when a number can't be trusted: too few cases, an uncalibrated judge, a missing assertion. Those
  gates say *invalid* instead of passing quietly."
- "Agents need more than prompt tests: we evaluate what the agent did, not just what it said."
