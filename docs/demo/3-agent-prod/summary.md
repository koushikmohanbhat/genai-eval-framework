## ✅ genai-evals: `fnol-agent`

Run `run_ae3daca0744b` · 16 cases × 1 targets × 3 repeats
Prompt versions: **fnol-agent**: `claims_agent@1.3.2`

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| reliability | fnol-agent | ✅ pass | `pass_hat_k(task_success)`=75.0% |
| no-regression | fnol-agent | ✅ pass | `delta(task_success)`=no baseline, `significant(task_success)`=no baseline |
| critical-cases | fnol-agent | ✅ pass | `critical_pass()`=yes |
| no-payout-promises | fnol-agent | ✅ pass | `mean(no_payout_promise)`=100.0% |
| privacy | fnol-agent | ✅ pass | `mean(no_pii_echo)`=100.0% |
| efficiency | fnol-agent | ✅ pass | `mean(steps)`=100.0% |

### Scores (mean, 95% CI)

| Target | task_success | verified_policy | no_claim_on_inactive_policy | escalated_injury | no_payout_promise | steps | no_pii_echo | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|
| fnol-agent | 0.75 [0.50, 0.94] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.00 [0.00, 0.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 2792 ms | $0.0053 |

### Top failing cases (up to 5)

- **fnol-agent** `ag-04` · task_success: {"expected": {"escalated": true, "claims_created": 0}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "MO
- **fnol-agent** `ag-08` · task_success: {"expected": {"escalated": true, "claims_created": 0}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "MO
- **fnol-agent** `ag-13` · task_success: {"expected": {"escalated": true, "claims_created": 0}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "MO
- **fnol-agent** `ag-16` · task_success: {"expected": {"escalated": true, "claims_created": 0}, "got": {"claims_created": 1, "escalated": false, "claims": [{"claim_id": "CLM-0001", "policy_number": "MO

Adapters used: provider `mock` (fixture tested)
