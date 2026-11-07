## ✅ genai-evals: `tool-agent`

Run `run_2fc32fbf6357` · 16 cases × 1 targets × 3 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| reliable-success | agent | ✅ pass | `pass_hat_k(task_success)`=100.0% |
| policy-edges | agent | ✅ pass | `critical_pass()`=yes |
| right-tools | agent | ✅ pass | `mean(correct_calls)`=100.0% |
| no-promises | agent | ✅ pass | `mean(no_promises)`=100.0% |
| privacy | agent | ✅ pass | `mean(no_pii)`=100.0% |
| efficient | agent | ✅ pass | `mean(steps)`=100.0% |
| latency-budget | agent | ✅ pass | `p95(latency_ms)`=2,144 |

### Scores (mean, 95% CI)

| Target | task_success | correct_calls | lookup_first | no_refund_over_limit | no_refund_undelivered | no_address_change_after_ship | escalated | steps | no_promises | no_pii | latency | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| agent | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 2144 ms | $0.0020 |

Adapters used: provider `mock` (fixture tested)
