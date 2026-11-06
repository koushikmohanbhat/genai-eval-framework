# Statistics and gates

## What is computed

- **Per case:** the mean over repeats. Aggregates are taken over cases, so repeats don't inflate *n*.
- **Bootstrap 95% CI** (2,000 resamples, seeded) on every grader mean.
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
| `p50/p95/p99(metric)`, `mean(metric)`, `total(metric)` | `latency_ms`, `ttft_ms`, `cost_usd`, `tokens`, `steps` |
| `delta(g)`, `delta_ci_low(g)`, `delta_ci_high(g)`, `significant(g)` | paired comparison with the baseline for the same target |
| `critical_pass()`, `error_rate()` | |

```yaml
gates:
  - {name: grounded,      rule: "mean(faithfulness) >= 0.9 and ci_low(faithfulness) >= 0.85"}
  - {name: no-regression, rule: "delta(task_success) > -0.03 or not significant(task_success)"}
  - {name: critical,      rule: "critical_pass()"}
  - {name: hindi,         rule: "mean(fields, tag='lang:hi') >= 0.9"}
  - {name: budget,        rule: "p95(latency_ms) <= 3000 and mean(cost_usd) <= 0.02", severity: warn}
  - {name: only-prod,     rule: "pass_hat_k(task_success) >= 0.8", targets: [openai]}
```

Without a baseline, `delta()` is 0 and `significant()` is false, so regression gates pass on a first run.
A gate whose grader has an untrusted judge (see calibration) is `invalid`.

## Why averages are not enough

In the demo, prompt 1.4.0 improves injury escalation (+1.00 on that slice) and breaks policy verification on lapsed
policies. Mean task success is unchanged (Δ 0.00, CI [-0.31, +0.38]), so a mean-only gate would ship it. The
`critical_pass()` gate blocks it.
