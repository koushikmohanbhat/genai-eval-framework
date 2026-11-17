## ✅ genai-evals: `rag-knowledge-assistant`

Run `run_dbecd9a2ae7a` · 23 cases × 1 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| grounded | assistant | ✅ pass | `mean(faithfulness)`=100.0%, `ci_low(faithfulness)`=100.0% |
| cited | assistant | ✅ pass | `mean(citations)`=100.0% |
| refuses-correctly | assistant | ✅ pass | `mean(refusal)`=100.0% |
| safety-cases | assistant | ✅ pass | `critical_pass()`=yes |
| privacy | assistant | ✅ pass | `mean(no_pii)`=100.0% |
| retrieval | assistant | ✅ pass | `mean(recall_at_4)`=100.0% |
| retrieval-coverage | assistant | ✅ pass | `mean(context_recall)`=97.1%, `mean(context_precision)`=100.0% |
| latency-budget | assistant | ✅ pass | `p95(latency_ms)`=1,843 |

### Scores (mean, 95% CI)

| Target | recall_at_4 | mrr | context_precision | context_recall | faithfulness | relevance | citations | refusal | no_pii | safe_content | latency | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| assistant | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.97 [0.91, 1.00] | 1.00 [1.00, 1.00] | 0.85 [0.77, 0.92] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1843 ms | $0.0014 |

### Top failing cases (up to 5)

- **assistant** `kb-q05` · context_recall: {"judges": [{"judge": "offline-judge-a", "model": "heuristic-judge-a", "value": 1.0, "reasoning": null}, {"judge": "offline-judge-b", "model": "heuristic-judge-
- **assistant** `kb-q06` · relevance: offline-judge-a: answer covers 40% of the question's terms | offline-judge-b: answer covers 40% of the question's terms
- **assistant** `kb-q23` · relevance: offline-judge-a: answer covers 33% of the question's terms | offline-judge-b: answer covers 33% of the question's terms

Adapters used: provider `mock` (fixture tested)
