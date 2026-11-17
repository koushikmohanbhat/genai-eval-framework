# Growing the eval set

An eval set is never finished. This page covers checking what a dataset doesn't test yet, filling gaps with generated
cases, and turning production failures into new cases, with expert review before any new case counts.

## Coverage tags

Tag cases with the standard coverage tags alongside your own domain tags:

```bash
genai-evals cases tags --workflow agent       # the tags that apply to a workflow, with descriptions
genai-evals cases coverage suite.yaml         # what this dataset has, and what it is missing
```

| Group | Tags |
|---|---|
| Difficulty | `typical`, `edge`, `adversarial` |
| Input | `multilingual`, `structured_input`, `image_input`, `noisy_input` (extraction) |
| Context | `multi_intent`, `typos`, `very_short`, `long_conversation`, `ambiguous_tool_output`, `bad_tool_args`, `circular_handoff`, `no_answer` (RAG) |
| Safety | `jailbreak`, `system_conflict` (the system prompt must win over the user), `format_request` |

Each tag applies to some workflows (`rag`, `agent`, `extraction`, `single_turn`); the workflow is taken from the
suite's `pack`. `coverage` reports:

- cases without a difficulty tag;
- the share of edge and adversarial cases (aim for at least 20%);
- applicable tags with no case.

`--strict` exits 1 if anything is missing, and `genai-evals doctor` includes the same check. Reports already break
every grader down by tag, so each slice gets its own pass rate.

The starter cases in each pack cover most tags. They leave gaps (for example `image_input`) that only the
customer's own data can fill.

## Draft cases

New cases from either path below are written as **drafts** (`review_status: draft`).
- **Excluded until promoted.** `load_cases` leaves drafts out of runs and gates until an expert has reviewed them.
  Set `include_draft_cases: true` on a suite to try them out.
- **How to promote.** Edit the drafts file to write each draft's `reference` and fix its tags, then promote:

```bash
genai-evals cases promote drafts.jsonl --into data/cases.jsonl --reviewer "ana@customer" --schema schema/case.schema.json
genai-evals cases promote drafts.jsonl --into data/cases.jsonl --ids gen-1a2b3c4d,prod-9f8e7d6c
```

`promote` does three things:
- It marks the case `reviewed` and records the reviewer.
- It drops the review-only metadata (the failing output).
- It adds `edge` if the case has no difficulty tag.

It refuses a draft with no reference unless you pass `--allow-empty-reference`, which is for suites graded only by
reference-free graders. Drafts that aren't promoted stay in the drafts file.

## Generate drafts for missing tags

```bash
genai-evals cases generate suite.yaml --generator judge-strong --n 5 --out drafts.jsonl            # the missing tags
genai-evals cases generate suite.yaml --generator judge-strong --tag multilingual --tag typos --n 10
```

- **The generator.** `--generator` is a provider profile; use a strong model. It gets the suite's description, the
  tag's definition and a few existing cases as examples of the format.
- **What it writes.** Varied inputs, each with a note on what a correct response must do (`metadata.expected`). The
  note is a starting point for the reviewer's reference, not the reference itself.
- **Personal data.** Generated inputs are tokenised like any other.

## Harvest drafts from production

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --out out/online
genai-evals cases harvest out/online/result.json --out drafts.jsonl                      # every failing sample
genai-evals cases harvest out/online/result.json --grader no_promises --below 0.5 --limit 30
```

- **What it takes.** Samples that failed (or scored below `--below` on) any grader, or only the graders named with
  `--grader`.
- **What a draft records.**
  - the grader(s) it failed, as `failed:<grader>` tags;
  - the run, original case, target, trace and session ids;
  - the failing output, for the reviewer.
- **Duplicates.** Duplicate inputs are kept once.
- **Personal data.**
  - Inputs and outputs are tokenised again with the built-in tokeniser. Set `GENAI_EVALS_TOKEN_KEY` so tokens match
    the rest of the project.
  - Harvested cases come from real users. Keep the drafts file out of version control until it has been reviewed.
  - With the DPDP framework, register promoted rows so an erasure request reaches them ([DPDP](dpdp.md)).

## A monthly loop

1. `cases harvest` from the last month's online runs.
2. An expert reviews the drafts: keeps the real failures, writes references, adds coverage tags.
3. `cases promote` into the dataset, then `cases coverage` and `cases generate` for anything still missing.
4. Re-run the suite against the baseline and update it if the system improved.
5. Every few months, re-label a sample and re-run `calibrate` ([human labels](human-labels.md)).
