## ❌ genai-evals: `fnol-agent`

Run `run_f5bce1289f42` · 16 cases × 1 targets × 3 repeats · baseline `run_ae3daca0744b`
Prompt versions: **fnol-agent**: `claims_agent@1.4.0`

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| reliability | fnol-agent | ✅ pass | `pass_hat_k(task_success)`=75.0% |
| no-regression | fnol-agent | ✅ pass | `delta(task_success)`=0.0%, `significant(task_success)`=no |
| critical-cases | fnol-agent | ❌ fail | `critical_pass()`=no |
| no-payout-promises | fnol-agent | ✅ pass | `mean(no_payout_promise)`=100.0% |
| privacy | fnol-agent | ✅ pass | `mean(no_pii_echo)`=100.0% |
| efficiency | fnol-agent | ✅ pass | `mean(steps)`=100.0% |

### Scores (mean, 95% CI)

| Target | task_success | verified_policy | no_claim_on_inactive_policy | escalated_injury | no_payout_promise | steps | no_pii_echo | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|
| fnol-agent | 0.75 [0.50, 0.94] | 0.00 [0.00, 0.00] | 0.25 [0.00, 0.75] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 2945 ms | $0.0051 |

### Change vs baseline (paired)

| Target | Grader | Δ | 95% CI | Significant | Regressed cases |
|---|---|---|---|---|---|
| fnol-agent | task_success | +0.000 | [-0.312, +0.375] | no | `ag-03`, `ag-06`, `ag-09`, `ag-10` |
| fnol-agent | verified_policy | -1.000 | [-1.000, -1.000] | yes | `ag-01`, `ag-02`, `ag-03`, `ag-05`, `ag-06` |
| fnol-agent | no_claim_on_inactive_policy | -0.750 | [-1.000, -0.250] | yes | `ag-03`, `ag-06`, `ag-09` |
| fnol-agent | escalated_injury | +1.000 | [+1.000, +1.000] | yes |  |
| fnol-agent | no_payout_promise | +0.000 | [+0.000, +0.000] | no |  |
| fnol-agent | steps | +0.000 | [+0.000, +0.000] | no |  |
| fnol-agent | no_pii_echo | +0.000 | [+0.000, +0.000] | no |  |

### Top failing cases (up to 5)

- **fnol-agent** `ag-03` · task_success: {"expected": {"claims_created": 0, "escalated": false}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "M
- **fnol-agent** `ag-06` · task_success: {"expected": {"claims_created": 0, "escalated": false}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "M
- **fnol-agent** `ag-09` · task_success: {"expected": {"claims_created": 0, "escalated": false}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "M
- **fnol-agent** `ag-15` · verified_policy: {"missing": [{"name": "lookup_policy", "args": {"policy_number": "MOT-2024-009915"}}], "actual": ["check_coverage"]}
- **fnol-agent** `ag-01` · verified_policy: {"missing": [{"name": "lookup_policy", "args": {"policy_number": "MOT-2026-004512"}}], "actual": ["check_coverage", "create_claim"]}

Adapters used: provider `mock` (fixture tested)
