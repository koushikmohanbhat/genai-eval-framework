# Statistics and gates

## What is computed

- **Per case:** the mean over repeats. Aggregates are taken over cases, so repeats don't inflate *n*.
- **Bootstrap 95% CI** (2,000 resamples over cases, seeded) on every grader mean.
- **pass@k** (passed at least once) and **pass^k** (passed every time). Use pass^k for agents in production.
- **Paired comparison** with a baseline on the same cases: Δ mean with a bootstrap CI, a McNemar exact p-value for
  pass/fail, the improved and regressed case lists, and a warning when *n* is too small to detect the difference.
- **Slices** by tag, **critical cases** (every non-skipped score must pass on every repeat), and **ops** (latency /
  TTFT p50 / p95 / p99, cost, tokens, steps, errors).

## Gate rules

Gate rules are a small sandboxed expression language (Python syntax, whitelisted functions, no attribute access,
imports or comprehensions).

| Function | Meaning |
|---|---|
| `mean(g)`, `ci_low(g)`, `ci_high(g)` | grader aggregate; `mean(g, tag='lang:hi')` for a slice |
| `pass_rate(g)`, `pass_at_k(g)`, `pass_hat_k(g)` | pass statistics |
| any of the above with `split='representative'` or `split='stress'` | the production-like set (weighted by `case.weight` when given) or the edge / adversarial suite; see [two sets](growing-the-eval-set.md#two-sets-representative-and-stress) |
| `p50/p95/p99(metric)`, `mean(metric)`, `total(metric)` | `latency_ms`, `ttft_ms`, `cost_usd`, `tokens`, `steps` |
| `delta(g)`, `delta_ci_low(g)`, `delta_ci_high(g)`, `significant(g, alpha=0.05)` | paired comparison with the baseline for the same target |
| `critical_pass()`, `error_rate()` | |

```yaml
gates:
  - {name: grounded,      rule: "mean(faithfulness, split='representative') >= 0.9 and ci_low(faithfulness, split='representative') >= 0.85"}
  - {name: stress,        rule: "mean(faithfulness, split='stress') >= 0.8"}
  - {name: no-regression, rule: "delta(task_success) > -0.03 or not significant(task_success)"}
  - {name: critical,      rule: "critical_pass()"}
  - {name: hindi,         rule: "mean(fields, tag='lang:hi') >= 0.9"}
  - {name: budget,        rule: "p95(latency_ms) <= 3000 and mean(cost_usd) <= 0.02", severity: warn}
  - {name: only-prod,     rule: "pass_hat_k(task_success) >= 0.8", targets: [openai]}
```

## Gate outcomes

Each gate is evaluated per target and gets one status:

| Status | When | Effect on the run |
|---|---|---|
| `pass` | the rule is true | none |
| `fail` | the rule is false | `block`: run fails (exit 1). `warn`: reported; run fails only with `--fail-on-warn`. `info`: reported as diagnostic, never fails |
| `invalid` | the rule can't be decided: no baseline for `delta()` / `significant()`, fewer than `min_n` scored cases (overall, in a tag slice or split, or in the baseline overlap), an untrusted judge (see [calibration](graders.md#calibration)), an unknown grader or a rule error | `block`: run fails (exit 1). `warn`: reported as a warning. `info`: reported as "too few cases (diagnostic)" |

**Severity.** `block` is the default.
- `warn` is for early warnings, such as an SLO or a retrieval drift alarm.
- `info` is diagnostic: use it for slices too small to support a decision, such as a stress tag with a handful of
  cases, so the number is visible without deciding a release.

`genai-evals cases coverage` and `doctor` list the gates whose slice has fewer cases than their `min_n` **before** a
run. A blocking gate in that list is a problem: add cases, or make it `info`.

Rules that apply to the numbers a gate reads:

- **Errors.** A target error, or a grader error, is a failed score with value 0 by default (`suite.on_error: fail`).
  `on_error: exclude` drops errored scores from means instead; the report still shows the error count and
  `error_rate()`. Excluding errors makes a broken system look better, so use it only for diagnosis.
- **Retries.** Provider calls are retried (`retries`, default 3, per provider profile) with backoff before they count
  as an error. Retried calls are not visible in scores.
- **Skipped scores** (a grader that doesn't apply to a case's tags) are left out of means and of `min_n`.
- **Minimum sample size.** `suite.min_n` (default 5) applies to every gate; override per gate with `min_n:`.
- **No baseline.** `delta()` and `significant()` make a gate `invalid` when there is no baseline result. On the first
  run of a new suite, pass `--allow-missing-baseline` (or set `allow_missing_baseline: true` on the gate); the gate
  then passes and its message says "no baseline". `--baseline` pointing at a file that doesn't exist is an error.
- **Partial runs.** There are none: a run either writes a complete result or exits with an error.

Exit codes for `genai-evals run` and `online`: **0** all blocking gates pass (and, with `--fail-on-warn`, all warn
gates too); **1** a blocking gate failed or was invalid; **2** configuration or input error (bad YAML, missing
environment variable, missing baseline file).

## Statistical assumptions

The intervals and tests are only as good as these assumptions. Read them before you put a number in front of a customer.

- **Cases are exchangeable.** The bootstrap resamples cases independently. If many cases come from the same customer,
  document or conversation, they are correlated and the CI is too narrow. Keep one case per unit where you can, or tag
  the cluster and gate on slices. Cluster-aware resampling is on the [roadmap](roadmap.md).
- **Repeats are averaged first.** Repeats reduce noise in each case's score; they never increase *n*.
- **Paired tests need the same cases.** `delta()` uses cases present in both runs; a gate is `invalid` if fewer than
  `min_n` overlap. McNemar uses per-case pass/fail; the bootstrap uses per-case means.
- **Multiple comparisons.** Every `significant()` in a suite is a separate test at `alpha` (default 0.05). With many
  graders × targets, some will be "significant" by chance. Set `gate_correction: holm` on the suite: every
  `significant()` across the run's gates and targets is then decided on Holm-adjusted p-values (paired bootstrap), so
  the chance of *any* false "significant" stays at `alpha`. Holm is uniformly more powerful than Bonferroni. The gate
  records each `p_holm(grader)`. Alternatively, gate on effect size (`delta_ci_low(g) > -0.03`) rather than on
  significance.
- **Small datasets.** With 30 cases, a 95% CI on a pass rate near 0.8 is roughly ±0.14. When a grader behind a
  blocking gate has an interval wider than ±10 points, the run adds a note with the number of cases that would give
  ±5. `min_n` stops gates deciding on a handful of cases; it does not make a small set representative.

## How many cases?

`genai-evals power` turns "how sure do we need to be" into a number of cases (normal approximation, pass/fail,
independent cases):

```bash
genai-evals power --rate 0.85 --delta 0.05 --half-width 0.05 --n 40 --discordant 0.1
#   pass rate 85%, alpha 0.05, power 80%
#     95% interval of ±5 points: 196 cases
#     detect a change of +5 points: 686 cases per arm (independent samples)
#       on the same cases, with 10% of verdicts differing: 312 cases (paired)
#     with 40 cases: interval ±11 points; smallest detectable change 22 points unpaired, 14 paired
#     for a safety claim, Anthropic suggests about 100 multi-turn or 1000 single-turn items
```

- **Paired designs need far fewer cases.** `delta()` compares the same cases, so what matters is the share of cases
  whose verdict differs between baseline and candidate (`--discordant`; read it from a previous comparison).
- **Repeats don't add cases.** They are averaged per case first.
- For **safety claims**, Anthropic's guidance is roughly 100 items for complex multi-turn evals and 1,000 for simple
  single-turn ones. The framework's minimums (30 representative cases, 5–10 per stress tag) are enough to start
  measuring, not to make such a claim.
- **Judges are measurement instruments.** A judge-graded mean is only as trustworthy as the judge's calibration.

## Gates and experiments

Gates answer "may this ship?" for one candidate against fixed thresholds and a baseline. Experiments answer "which of
these changes should we make?" for several candidates at once, with corrected significance; they apply the suite's
absolute gates to every variant and replace `delta()` gates with the side-by-side comparison. See
[experiments](experiments.md).

## Representative and stress results

Every grader is reported overall, per tag, and per split:

- **representative:** production-like cases, weighted by `case.weight` when given;
- **stress:** cases tagged `edge` / `adversarial`, or marked `split: stress`.

Gate the headline on the representative split, because that is the number that estimates production quality.
Gate the stress suite separately. An overall mean mixes the two in whatever proportion the dataset happens to have,
so adding edge cases lowers it without production getting worse. `genai-evals doctor` flags blocking gates that do
this. Requirements that every case must meet (`>= 1.0`) aren't averages, and are fine across both.

The weighted representative mean is a ratio estimate (Σ w·v / Σ w). Its CI comes from a bootstrap that resamples
cases together with their weights.

## Why averages are not enough

In the demo, prompt 1.4.0 improves injury escalation (+1.00 on that slice) and breaks policy verification on lapsed
policies. Mean task success is unchanged (Δ 0.00, CI [-0.31, +0.38]), so a mean-only gate would ship it. The
`critical_pass()` gate blocks it.
