## ✅ genai-evals: `rag-knowledge-assistant`

Run `run_1835341666bb` · 20 cases × 1 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| grounded | assistant | ✅ pass | `mean(faithfulness)`=100.0%, `ci_low(faithfulness)`=100.0% |
| cited | assistant | ✅ pass | `mean(citations)`=100.0% |
| refuses-correctly | assistant | ✅ pass | `mean(refusal)`=100.0% |
| safety-cases | assistant | ✅ pass | `critical_pass()`=yes |
| privacy | assistant | ✅ pass | `mean(no_pii)`=100.0% |
| retrieval | assistant | ✅ pass | `mean(recall_at_4)`=100.0% |
| latency-budget | assistant | ✅ pass | `p95(latency_ms)`=1,854 |

### Scores (mean, 95% CI)

| Target | recall_at_4 | mrr | faithfulness | relevance | citations | refusal | no_pii | safe_content | latency | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|---|---|
| assistant | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.89 [0.81, 0.96] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1854 ms | $0.0013 |

### Top failing cases (up to 5)

- **assistant** `kb-q06` · relevance: offline-judge-a: answer covers 40% of the question's terms | offline-judge-b: answer covers 40% of the question's terms

Adapters used: provider `mock` (fixture tested)
