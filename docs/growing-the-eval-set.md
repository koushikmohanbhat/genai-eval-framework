# Growing the eval set

An eval set is never finished. This page covers checking what a dataset doesn't test yet, filling gaps with generated
cases, and turning production failures into new cases, with expert review before any new case counts.

## Two sets: representative and stress

A suite holds two kinds of case, and they answer different questions:

| | Representative set | Stress suite |
|---|---|---|
| Question it answers | how good is the system on the traffic it will actually get? | where does it break? |
| Cases | production-like: ideally a random sample of real (tokenised) traffic, in production proportions | edge and adversarial cases written to find failures |
| How a case gets there | no stress tag, or `split: representative` | tagged `edge` or `adversarial` (`suite.stress_tags`), or `split: stress` |
| Weighting | `weight:` per case, when the sample isn't in production proportions (e.g. 3 for a case that stands for a three-times-more-common intent); the mean and its CI are then production-weighted | unweighted |
| Gates | the headline: `mean(g, split='representative')` | separate, usually with lower thresholds or per tag: `mean(g, split='stress')`, `critical_pass()` |

Averaging the two together gives a number that is neither: every edge case you add lowers the "quality" score
without production getting any worse. So every grader is reported per split, as well as overall, and per tag:

- in the result: `summary[target].graders[g].splits`, with representative and stress means, CIs, pass rates and `n`;
- in the HTML and Markdown reports, next to each grader;
- on the scorecard, where the headline is the representative number and the stress suite result sits beside it.

The packs' headline gates use `split='representative'`. `doctor` flags any blocking gate that averages across both
splits. It doesn't flag `>= 1.0` requirements such as "no PII on any case", because a requirement that every case
passes isn't an average.

## Making "representative" verifiable

A case without a stress tag is representative by default, but calling a set representative is a claim, and
`doctor` asks for evidence:

- **Provenance on the suite.** A `representative_sample:` block, required: it says what the set was sampled from,
  when and how, and how any weights were set.
- **Production cases.** At least `min_representative_production_share` (default 80%) of representative cases have
  `source: production`. Synthetic and SME-written cases can be in the set, but they can't make up most of it.

```yaml
representative_sample:
  source: production
  population: "all web-chat claim sessions in the production tenant"
  window: "2026-10-01..2026-10-31"
  method: "stratified random sample of 200 sessions by intent, proportional allocation"
  weights: "inverse inclusion: sessions per intent stratum / sessions drawn"
  drawn_by: "eval owner"
  drawn_at: "2026-11-02"
```

The easiest way to get both is to sample the set from an online run over production telemetry:

```bash
genai-evals online --sources sources.yaml --suite online_checks.yaml --out out/online
genai-evals cases sample out/online/result.json --n 200 --by-tag --population "web-chat claim sessions" --out rep.jsonl
#   wrote 200 draft representative case(s) to rep.jsonl, and their provenance to rep.jsonl.provenance.yaml
```

`cases sample` draws units (sessions, or case and target), **not** scores, so the set reflects traffic rather than
failures. With `--by-tag` it uses proportional stratified sampling. Every draft has `source: production`,
`split: representative`, and its run, session and trace ids.

### Weights: from the sample back to production

A session reaches the eval set through three filters, and each case's weight is the inverse of the chance it got
through all of them:

```
P(in the set) = capture_rate      share of production sessions the telemetry captured (trace sampling, content capture)
              x sampling          share of captured sessions the online run graded (sources.yaml)
              x selection         share of graded units `cases sample` drew (n / N, per tag stratum with --by-tag)
weight        = 1 / P(in the set)
```

So a source captured at 10% and one captured in full are weighted back to their production proportions, and the
`representative` split's weighted mean estimates production. Each draft records the three factors in
`metadata.inclusion`. When every unit has the same probability (one source, simple random sample) the weights carry no
information and are left out.

Set `capture_rate` per source in `sources.yaml` from the telemetry settings (the trace sampler ratio, or the share of
invocations with content logging). If it is not set, the framework cannot know how much traffic the telemetry missed,
and the provenance says so:

```yaml
represents: "the captured telemetry sample only: the telemetry capture rate of claims-agent-aws is unknown (set capture_rate in sources.yaml)"
```

The CLI prints the same note. A set like that still describes the captured traffic, but not production. Capture that
drops sessions *non-randomly* (errors not traced, a channel without instrumentation) is selection bias that no weight
can fix: see [what online scores mean](sources.md#what-online-scores-mean).

The provenance file records:
- the window;
- each source's capture rate, online sampling rate and method, and their product (`inclusion_probability`);
- the selection method, how the weights were set, and what the set `represents`.

An SME writes each reference, `cases promote` adds the cases (they stay `typical`, not `edge`), and you copy the
provenance block into `suite.yaml`. Sampling from recorded fixture telemetry is labelled as such and doesn't count
as production.

## Coverage tags (the stress suite)

Tag cases with the standard coverage tags alongside your own domain tags:

```bash
genai-evals cases tags --workflow agent       # the tags that apply to a workflow, with descriptions
genai-evals cases coverage suite.yaml         # both sets: their sizes, and what the stress suite is missing
```

| Group | Tags |
|---|---|
| Difficulty | `typical`, `edge`, `adversarial` (`edge` / `adversarial` put a case in the stress suite) |
| Input | `multilingual`, `structured_input`, `image_input`, `noisy_input` (extraction) |
| Context | `multi_intent`, `typos`, `very_short`, `long_conversation`, `ambiguous_tool_output`, `bad_tool_args`, `circular_handoff`, `no_answer` (RAG) |
| Safety | `jailbreak`, `system_conflict` (the system prompt must win over the user), `format_request` |

Each tag applies to some workflows (`rag`, `agent`, `extraction`, `single_turn`); the workflow is taken from the
suite's `pack`. `coverage` (and the **eval set** check in `genai-evals doctor`) reports:

- cases without a difficulty tag or split;
- the representative set's size (at least `min_representative_cases`, default 30) and its **evidence** (below);
- for the stress suite, per coverage tag: enough cases, **thin** (some, but fewer than needed), or missing;
- blocking gates that mix the two sets;
- gates whose tag or split slice has fewer cases than their `min_n`, so they will be invalid. A blocking one is a
  problem; `warn` and `info` ones are listed as notes.

The starter packs fail most of these on purpose. Their representative cases are synthetic and few, and their stress
cases are one or two per tag, to show the shape. A real engagement fills both from the customer's data.

**How many cases a tag needs.** A tag present once shows the scenario exists. It doesn't support a conclusion
about how the system handles it. So each coverage tag has a minimum by risk tier:

| Tier | Tags | Minimum (default) |
|---|---|---|
| high | `jailbreak`, `system_conflict`, `bad_tool_args`, `circular_handoff` | 10 |
| standard | every other coverage tag | 5 |

Change them with `stress_min_cases: {high: 10, standard: 5}`, or per tag with `stress_min_by_tag: {multilingual: 20}`.
A tag below its minimum is reported as **thin**. Gates on thin slices should be `severity: info` (diagnostic): they
are reported, but they don't decide a release ([gates](gates.md#gate-outcomes)).

A coverage tag on a typical case (say, a common Hindi question) is fine: tags describe the case, the split says how it
is used. If a common pattern belongs in the production estimate, keep it representative (`split: representative`)
even when it carries a coverage tag.

`--strict` exits 1 if anything is missing.

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
- It adds `edge` if the case has no difficulty tag, so generated and harvested cases join the stress suite, not the production estimate.

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

1. Refresh the representative set with `cases sample` from recent production traffic, so its proportions follow
   production, and update `representative_sample:`.
2. `cases harvest` from the last month's online runs.
3. An expert reviews the drafts: keeps the real failures, writes references, adds coverage tags. Harvested
   failures go in the **stress** suite (tag them `edge`): they were selected because they failed, so they would bias
   the production estimate.
4. `cases promote` into the dataset, then `cases coverage` and `cases generate` for anything still missing.
5. Re-run the suite against the baseline and update it if the system improved.
6. Every few months, re-label a sample and re-run `calibrate` ([human labels](human-labels.md)).
