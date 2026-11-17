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

## 2. Make the sheet

```bash
genai-evals labels sheet out/live/result.json --grader faithfulness --n 60 --reviewers ana,bo,cy --out reviews.csv
```

- Items are sampled at random, **stratified by tag** and balanced between judge passes and fails, so agreement is
  measured on both outcomes and on the rare slices. `--no-stratify` for a plain random sample.
- One row per item per reviewer. Reviewers fill `label` (`pass`, `fail` or `unsure`) and `notes`.
- **The judge's verdict is not in the sheet**, so labels stay independent of the judge.
- Inputs and outputs come from the result, which is already tokenised when the run used tokenisation.

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
genai-evals calibrate out/live/result.json --grader faithfulness --labels reviews.csv --out calibration.json --min-kappa 0.4
```

`--labels` takes either a single-reviewer file (`case_id,target,label`) or the multi-reviewer sheet, which is
combined into consensus labels first (`--rule` as above). The calibration records the reviewers' agreement next to
the judge's, and warns if the judge agrees with the consensus suspiciously more than the reviewers agree with each
other (a sign the labels aren't independent). Trust rules are in [graders](graders.md#calibration).

## The judge ladder

OpenAI's guidance: start with a strong judge, check it against human labels, and move to a cheaper judge only once it
agrees with the humans as well. Define both judges as graders with the same rubric and different `judges:`:

```yaml
graders:
  - {name: quality, type: rubric, rubric: "...", judges: [judge-strong]}
  - {name: quality_cheap, type: rubric, rubric: "...", judges: [judge-cheap]}
```

```bash
genai-evals calibrate out/live/result.json --grader quality_cheap --compare-to quality --labels reviews.csv
#   quality_cheap vs quality on 120 labelled items: kappa 0.71 vs 0.74 (difference -0.03, 95% CI -0.09 to +0.03); judge cost ...
#   approved: not meaningfully worse than the stronger judge
```

The cheaper judge is approved when, on the same labelled items:

- the 95% lower bound of κ(cheap) − κ(strong) is above −`--margin` (default 0.05), so it is not meaningfully worse;
- at least `--min-n` items (default 30) are shared; and
- with `--min-kappa`, its own κ lower bound clears that threshold.

The command exits 1 when it is not approved. Non-inferiority needs a decent label set: with 60 items the interval on
the difference is usually wider than ±0.1, so plan for 150–200 labels if you want to switch judges. `--compare-result`
takes the strong judge's grader from another run on the same cases.
