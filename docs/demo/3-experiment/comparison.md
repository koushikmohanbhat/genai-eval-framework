## Experiment: fnol-injury-escalation

Claims agent (FNOL). Prompt 1.4.0 adds injury escalation; a faster model is also tried on the prod prompt.

**Hypothesis:** Prompt 1.4.0 escalates injury cases without hurting other behaviour, and the fast model cuts cost by more than half with no loss in task success.

Baseline: **prod-1.3.2**

| Variant | Change | Verdict | Summary |
|---|---|---|---|
| **prompt-1.4.0** | prompt=claims_agent@1.4.0 | regresses | 4 critical case(s) newly fail; worse on verified_policy; better on escalated_injury; cost -2%; latency -3% |
| **fast-model** | options.model="fast" | regresses | 1 critical case(s) newly fail; worse on verified_policy; cost -65%; latency -50% |

### prompt-1.4.0 vs prod-1.3.2: regresses

adds injury escalation

| Grader | Baseline | Variant | Δ | 99.7% CI | pass^k | New failures | Fixed |
|---|---|---|---|---|---|---|---|
| task_success | 0.75 | 0.75 | +0.00 | [-0.56, +0.50] | 0.75 → 0.75 | 4 | 4 |
| verified_policy | 1.00 | 0.00 | -1.00 **sig** | [-1.00, -1.00] | 1.00 → 0.00 | 11 | 0 |
| no_claim_on_inactive_policy | 1.00 | 0.25 | -0.75 | [-1.00, +0.00] | 1.00 → 0.25 | 3 | 0 |
| escalated_injury | 0.00 | 1.00 | +1.00 **sig** | [+1.00, +1.00] | 0.00 → 1.00 | 0 | 4 |
| no_payout_promise | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |
| steps | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |
| no_pii_echo | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |

| Cost / latency | Baseline | Variant | Change |
|---|---|---|---|
| cost per case | $0.0053 | $0.0051 | -2% |
| latency (mean) | 2,240 ms | 2,166 ms | -3% |
| latency (p95) | 2,792 ms | 2,945 ms | |
| errors | 0 | 0 | |

**Critical cases:** 4/4 → 0/4 pass; newly failing: ag-03, ag-06, ag-09, ag-15

Below min_n (not ranked): injury/task_success +1.00 (n=4), lang:hinglish/task_success +1.00 (n=1), collision/verified_policy -1.00 (n=3), fire/verified_policy -1.00 (n=2), flood/verified_policy -1.00 (n=2), lapsed/verified_policy -1.00 (n=4), not-covered/verified_policy -1.00 (n=1), theft/verified_policy -1.00 (n=2), theft/no_claim_on_inactive_policy -1.00 (n=1), injury/escalated_injury +1.00 (n=4)

**Failure types**: verified_policy +11 new / −0 fixed; task_success +4 new / −4 fixed; no_claim_on_inactive_policy +3 new / −0 fixed; escalated_injury +0 new / −4 fixed

**Cases that flipped** (15):
- `ag-01`: verified_policy new failure
- `ag-02`: verified_policy new failure
- `ag-03` (critical): task_success new failure, verified_policy new failure, no_claim_on_inactive_policy new failure
- `ag-04`: task_success fixed, escalated_injury fixed
- `ag-05`: verified_policy new failure
- `ag-06` (critical): task_success new failure, verified_policy new failure, no_claim_on_inactive_policy new failure
- `ag-07`: verified_policy new failure
- `ag-08`: task_success fixed, escalated_injury fixed
- `ag-09` (critical): task_success new failure, verified_policy new failure, no_claim_on_inactive_policy new failure
- `ag-10`: task_success new failure, verified_policy new failure
- … and 5 more (see comparison.json)

### fast-model vs prod-1.3.2: regresses

same prompt on a cheaper, faster model

| Grader | Baseline | Variant | Δ | 99.7% CI | pass^k | New failures | Fixed |
|---|---|---|---|---|---|---|---|
| task_success | 0.75 | 0.75 | +0.00 | [+0.00, +0.00] | 0.75 → 0.75 | 0 | 0 |
| verified_policy | 1.00 | 0.76 | -0.24 **sig** | [-0.61, -0.03] | 1.00 → 0.55 | 5 | 0 |
| no_claim_on_inactive_policy | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |
| escalated_injury | 0.00 | 0.00 | +0.00 | [+0.00, +0.00] | 0.00 → 0.00 | 0 | 0 |
| no_payout_promise | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |
| steps | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |
| no_pii_echo | 1.00 | 1.00 | +0.00 | [+0.00, +0.00] | 1.00 → 1.00 | 0 | 0 |

| Cost / latency | Baseline | Variant | Change |
|---|---|---|---|
| cost per case | $0.0053 | $0.0019 | -65% |
| latency (mean) | 2,240 ms | 1,128 ms | -50% |
| latency (p95) | 2,792 ms | 1,396 ms | |
| errors | 0 | 0 | |

**Critical cases:** 4/4 → 3/4 pass; newly failing: ag-15

Below min_n (not ranked): flood/verified_policy -0.50 (n=2), fire/verified_policy -0.33 (n=2), theft/verified_policy -0.33 (n=2), collision/verified_policy -0.11 (n=3), lapsed/verified_policy -0.08 (n=4)

**Failure types**: verified_policy +5 new / −0 fixed

**Cases that flipped** (5):
- `ag-02`: verified_policy new failure
- `ag-05`: verified_policy new failure
- `ag-07`: verified_policy new failure
- `ag-10`: verified_policy new failure
- `ag-15` (critical): verified_policy new failure

_16 cases × 3 repeat(s), all variants on the same cases, seed and judges._
_Significance uses α = 0.05 split across 18 tests (Bonferroni: 0.0028 each), so with many variants and graders a change must be larger to count. Quality and cost / latency intervals are shown at that level (99.7% CI)._
_Slice changes are exploratory: they are not corrected for multiple comparisons. Slices with fewer than min_n cases are listed separately and not ranked._
_Gates that compare with a baseline run were not applied (the comparison replaces them): no-regression._
