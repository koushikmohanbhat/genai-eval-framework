# Human labels and the judge ladder

Model judges are only as good as their agreement with people who know the domain. This page covers getting reliable
human labels (several reviewers, examples at each level, consensus) and using them to decide which judge to trust,
including when a cheaper judge can replace a stronger one.

## 1. Write the labelling guide

```bash
genai-evals labels guide suite.yaml --grader faithfulness --out labelling-guide-faithfulness.md
```

The guide is built from the grader's rubric and `anchors`. It explains what to judge, the pass threshold, and has a
section for an example at each level. **Fill in the examples before anyone labels**: reviewers agree more when each
level is shown, not just described. Put the same examples in the grader's `anchors` so the judge sees them too.

## 2. Make the sheets: a calibration sample and a validation sample

**The unit is (case, target).** Each unit gets one human label.
- When the run had repeats, the sheet picks one repeat per unit at random and records it in the `repeat` column.
- Calibration compares the label with the judge's verdict on that repeat only, so repeats never count as extra human
  comparisons.
- Labels files without a `repeat` column are compared with repeat 0. If that unit had several repeats, the
  calibration warns.

Two samples answer two different questions, so draw both:

```bash
# calibration: is the judge right on both outcomes and on every slice?
genai-evals labels sheet out/live/result.json --grader faithfulness --n 60 --reviewers ana,bo,cy --out calibration.csv
# validation: how often is the judge right on production-like traffic?
genai-evals labels sheet out/live/result.json --grader faithfulness --n 40 --design random --reviewers ana,bo --out validation.csv
```

| | Calibration sample (`--design stratified`, the default) | Validation sample (`--design random`) |
|---|---|---|
| How items are drawn | stratified by tag and balanced between the judge's passes and fails | simple random sample of the scored items |
| Good for | agreement on both outcomes and on rare slices (a random sample of a 95%-pass system has almost no fails to check) | an estimate of agreement, and of the human pass rate, as they are in production |
| Not good for | estimating production prevalence or overall agreement: rare slices and fails are over-represented | small or rare slices |

- **Which result to sample from.** Run on the representative set (or on online production samples) for the
  validation sheet. A sample of a suite that includes the stress cases is "random" only over that suite.
- **Weights and strata.** Every row records `sample_design`, `stratum` and `weight`. The weight is the inverse
  inclusion weight: the number of units the labelled unit stands for (units in its stratum ÷ units drawn from it).
  Calibration uses them in two ways:
  - to estimate agreement at the population's prevalence (weighted κ, accuracy and pass rates);
  - for **design-aware intervals**: the bootstrap resamples units *within each stratum*, keeping stratum sizes and
    weights, as the sample was drawn.

  Leave these columns as they are.
- **Rows.** One row per item per reviewer. Reviewers fill `label` (`pass`, `fail` or `unsure`) and `notes`.
- **Independence.** The judge's verdict is not in the sheet, so labels stay independent of the judge.
- **Personal data.** Inputs and outputs come from the result, which is already tokenised when the run used
  tokenisation.

## 3. Measure agreement between reviewers

```bash
genai-evals labels agreement reviews.csv --out consensus.csv
```

Reports raw agreement and Fleiss' κ on items with two or more reviewers, each reviewer's agreement with the
consensus, and the disputed items. Consensus rules:

- `--rule majority` (default): the majority label; ties are disputed.
- `--rule unanimous`: any disagreement is disputed.
- A row with `adjudicated` = true always decides (the adjudicator's call on a disputed item).
- A later `round` from the same reviewer replaces their earlier label.

**Rounds.** Round 1 is independent. Discuss the disputed items, update the labelling guide where it was unclear,
re-label them as round 2, and send what's still disputed to an adjudicator. `--fail-on-dispute` exits 1 while
anything is disputed, for a CI check on the labels file.

If reviewers agree only moderately (Fleiss' κ below 0.6) the tool warns: tighten the guide first. A judge can't be
held to a standard the humans don't meet.

## 4. Calibrate the judge

```bash
genai-evals calibrate out/live/result.json --grader faithfulness --labels calibration.csv \
    --validation-labels validation.csv --out calibration.json --min-kappa 0.4
#   faithfulness: n=60 accuracy=0.883 kappa=0.71 (95% CI 0.52–0.86) on the stratified calibration sample -> calibration.json
#     production-weighted: kappa 0.66, accuracy 94%, human pass rate 91% (sample: 52%)
#     representative validation sample: n=40, accuracy 93%, kappa 0.62, human pass rate 90%
#     slice lang:hi: n=12, accuracy 75%, kappa 0.48
#     slice split:stress: n=24, accuracy 83%, kappa 0.66
#     warning: judge agrees noticeably less on: lang:hi (75%, n=12)
```

`--labels` takes either a single-reviewer file (`case_id,target,label`) or the multi-reviewer sheet. A multi-reviewer
sheet is combined into consensus labels first (`--rule` as above). The calibration reports four things.

**Agreement on the calibration sample.** Accuracy, Cohen's κ with a bootstrap CI, positive and negative agreement,
and the confusion matrix. The sample was designed, so the human pass rate here (52% above) is not production
prevalence. The tool says so when the design is stratified.

**A production-weighted estimate**, from the sheet's weights: weighted κ, accuracy, and the human and judge pass
rates. The estimate is only as good as the population the sheet was drawn from (see above).

**Agreement per slice.** Agreement for every tag, and for `split:representative` and `split:stress`, with `n`.
Slices with at least 5 items and accuracy more than 15 points below the overall figure are warned about. A judge that
is fine overall but poor on Hindi questions, or on the stress suite, shouldn't gate those slices.

**The representative validation sample** (`--validation-labels`). It is reported separately and never pooled with
the calibration sample. When it is given, a trusted judge must clear `--min-kappa` (its CI lower bound) on **both**
samples. If it isn't given and the calibration sample is stratified (or of unknown design), the tool recommends
labelling one.

The calibration also records the reviewers' agreement next to the judge's. It warns if the judge agrees with the
consensus suspiciously more than the reviewers agree with each other, a sign the labels aren't independent. Trust
rules are in [graders](graders.md#calibration).

## The judge ladder

OpenAI's guidance: start with a strong judge, check it against human labels, and move to a cheaper judge only once it
agrees with the humans as well. Define both judges as graders with the same rubric and different `judges:`:

```yaml
graders:
  - {name: quality, type: rubric, rubric: "...", judges: [judge-strong]}
  - {name: quality_cheap, type: rubric, rubric: "...", judges: [judge-cheap]}
```

```bash
genai-evals calibrate out/live/result.json --grader quality_cheap --compare-to quality \
  --labels reviews.csv --validation-labels random-reviews.csv
#   quality_cheap vs quality on 120 labelled units (validation sample): kappa 0.71 vs 0.74 (difference -0.03, 95% CI -0.09 to +0.03); judge cost ...
#     quality_cheap: graded 120/121 labelled units (1% failed: 1 errors, 0 skipped, 0 missing)
#     quality: graded 121/121 labelled units (0% failed: 0 errors, 0 skipped, 0 missing)
#     counting ungraded units as disagreements: difference -0.04, 95% CI -0.10 to +0.02
#   approved: not meaningfully worse than the stronger judge (validation sample)
```

The cheaper judge is approved when, on the labelled units (case and target, one repeat each):

- the 95% lower bound of κ(cheap) − κ(strong) is above −`--margin` (default 0.05) on the units **both** judges graded;
- the same holds when **every gradable unit** is counted and a unit a judge failed to grade is treated as a
  disagreement with the human (see below);
- the cheaper judge failed to grade at most `--max-judge-failure` of the units (default 5%);
- at least `--min-n` units (default 30) are shared; and
- with `--min-kappa`, its own κ lower bound clears that threshold.

### Judges that fail to grade

A judge can time out, hit a rate limit, return something unparseable, or skip a unit whose data it needs. Those units
have no verdict, so they can't be compared, and they are often the hard ones. A judge that fails on 10% of units can
look *better* than the strong judge on the rest. So the ladder reports, per judge, what happened to every labelled unit:

| Status | Meaning | Counts against the judge |
|---|---|---|
| scored | the judge returned a verdict | – |
| transient error | rate limit, timeout, 5xx, connection error: **retry it** | yes, until retried |
| persistent error | unparseable verdict, schema or auth error, bad request: retrying the same call won't help | yes |
| skipped | the grader skipped the unit | yes |
| missing | no score for the unit (the grader didn't run on it) | yes |
| target error | the system under test failed, so there was nothing to judge | no: left out for both judges |

Errors are classified with the same rules the providers use for their own retries; a judge panel's error is transient
only if every judge's error was. New results record the class on each score (`error_kind`); older results are
classified from the error text.

Coverage is scored / gradable units, and the failure rate is the rest. Approval needs both the failure-rate limit
and the worst-case comparison, so a judge can't pass by failing on the cases it would get wrong. `calibrate` (one
judge) reports the same `coverage`, warns when more than 5% of labelled units weren't graded, and points to
`regrade` when any failed transiently.

### Retry transient failures before deciding

Provider calls already retry transient errors a few times (`retries` in the profile). A long rate-limit window can
still outlast them. Re-grade those units, without re-running the system under test, and compare again:

```bash
genai-evals regrade out/live/result.json --suite suite.yaml --grader quality_cheap
#   re-graded 18 unit(s) (transient) for quality_cheap in 23 attempt(s): 17 recovered, 1 still transient, 0 persistent
genai-evals calibrate out/live-regraded/result.json --grader quality_cheap --compare-to quality --labels reviews.csv
```

- `--only transient` (default) retries rate limits, timeouts and 5xx. `--only errors` also retries persistent errors,
  for example after fixing a judge's output format. `--only unscored` also grades units with no score.
- Each unit is tried up to `--attempts` times (default 3) with exponential backoff from `--backoff` seconds (default
  2). A persistent error stops the retries for that unit.
- The new result keeps the old samples, replaces only the re-graded scores, and recomputes summaries, gates and
  reports. It records `regraded_from` (the original run id), and per score `attempts` and `regraded`.
- It refuses if the grader's config changed since the run, because the scores would then mix two versions of the
  grader (`--force` overrides). With tokenisation on, the stored samples are already tokenised; judges saw tokens
  in the original run too.

When transient errors are what stand between a cheaper judge and approval, the ladder doesn't reject it. It returns
`decision: retry_first` with the `regrade` command to run. This happens only when the most favourable retry outcome
(every retried unit comes back right) would pass every check. Otherwise the decision is `not_approved`, and the
output says retrying can't change it. Exit code is 1 for both, 0 only when approved.

### Which sample decides

The calibration sheet is usually stratified: it over-samples rare tags and failures. Two judges can tie on that
sample and still differ on production traffic, if one is weaker on the common cases the sheet under-weights. So the
comparison respects how the labels were drawn, and reports the basis it used (`basis` in the JSON output):

| Labels given | Decided on |
|---|---|
| `--validation-labels` (a random sample, `labels sheet --design random`) | **the validation sample.** It is the unbiased estimate of production agreement. The calibration sample is reported alongside, with a note if the cheaper judge looks worse there |
| a stratified sheet with weights (`labels sheet` writes them) | the calibration sample, with production-weighted κ and a bootstrap that resamples within each stratum |
| a stratified sheet without weights | not approved: the sample can't estimate production; add `--validation-labels` |
| no recorded design | the calibration sample, as is (treat with care if the items were hand-picked) |

A random validation sample is the recommended basis: weights correct the sheet's proportions, but only a random
sample checks the cases the strata didn't anticipate.

The command exits 1 when it is not approved. Non-inferiority needs a decent label set: with 60 items the interval on
the difference is usually wider than ±0.1, so plan for 150–200 labels if you want to switch judges. `--compare-result`
takes the strong judge's grader from another run on the same cases.
