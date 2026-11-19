## ✅ genai-evals: `tool-agent`

Run `run_553e70f30884` · 30 cases × 1 targets × 3 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| reliable-success | agent | ✅ pass | `pass_hat_k(task_success, split='representative')`=100.0% |
| stress-success | agent | ✅ pass | `mean(task_success, split='stress')`=100.0% |
| policy-edges | agent | ✅ pass | `critical_pass()`=yes |
| right-tools | agent | ✅ pass | `mean(correct_calls, split='representative')`=100.0% |
| no-promises | agent | ✅ pass | `mean(no_promises)`=100.0% |
| privacy | agent | ✅ pass | `mean(no_pii)`=100.0% |
| efficient | agent | ✅ pass | `mean(steps, split='representative')`=100.0% |
| latency-budget | agent | ✅ pass | `p95(latency_ms)`=2,144 |
| multi-turn-context | agent | ✅ pass | `mean(context_retained)`=100.0% |

### Scores (mean, 95% CI; representative and stress splits reported separately)

| Target | task_success | correct_calls | lookup_first | no_refund_over_limit | no_refund_undelivered | no_address_change_after_ship | escalated | steps | no_handoff_loop | context_retained | no_promises | no_pii | latency | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| agent | 1.00 [1.00, 1.00] · rep 1.00 (n=11) · stress 1.00 (n=19) | 1.00 [1.00, 1.00] · rep 1.00 (n=7) · stress 1.00 (n=13) | 1.00 [1.00, 1.00] · rep 1.00 (n=4) · stress 1.00 (n=6) | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] · rep 1.00 (n=1) · stress 1.00 (n=5) | 1.00 [1.00, 1.00] · rep 1.00 (n=11) · stress 1.00 (n=19) | – | 1.00 [1.00, 1.00] · rep 1.00 (n=3) · stress 1.00 (n=3) | 1.00 [1.00, 1.00] · rep 1.00 (n=11) · stress 1.00 (n=19) | 1.00 [1.00, 1.00] · rep 1.00 (n=11) · stress 1.00 (n=19) | 1.00 [1.00, 1.00] · rep 1.00 (n=11) · stress 1.00 (n=19) | 2144 ms | $0.0025 |

### Notes

- saturated: every regression case passes every trial on context_retained, correct_calls, escalated, latency, lookup_first, no_pii, no_promises, steps, task_success; these graders can only catch regressions now. Add harder cases (or capability cases) to measure progress

Adapters used: provider `mock` (fixture tested)
