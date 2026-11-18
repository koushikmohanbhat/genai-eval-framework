## ✅ genai-evals: `rag-knowledge-assistant`

Run `run_d5de957055eb` · 25 cases × 1 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| grounded | assistant | ✅ pass | `mean(faithfulness, split='representative')`=100.0%, `ci_low(faithfulness, split='representative')`=100.0% |
| grounded-under-stress | assistant | ✅ pass | `mean(faithfulness, split='stress')`=100.0% |
| cited | assistant | ✅ pass | `mean(citations, split='representative')`=100.0% |
| refuses-correctly | assistant | ✅ pass | `mean(refusal, split='stress')`=100.0% |
| safety-cases | assistant | ✅ pass | `critical_pass()`=yes |
| privacy | assistant | ✅ pass | `mean(no_pii)`=100.0% |
| retrieval | assistant | ✅ pass | `mean(recall_at_4, split='representative')`=100.0% |
| retrieval-coverage | assistant | ✅ pass | `mean(context_recall, split='representative')`=96.7%, `mean(context_precision, split='representative')`=100.0% |
| latency-budget | assistant | ✅ pass | `p95(latency_ms)`=1,843 |

### Scores (mean, 95% CI; representative and stress splits reported separately)

| Target | recall_at_4 | mrr | context_precision | context_recall | faithfulness | relevance | citations | refusal | no_pii | safe_content | latency | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| assistant | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=4) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=4) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=4) | 0.97 [0.92, 1.00] · rep 0.97 (n=15) · stress 1.00 (n=4) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=4) | 0.85 [0.78, 0.91] · rep 0.87 (n=15) · stress 0.78 (n=4) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=4) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=10) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=10) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=10) | 1.00 [1.00, 1.00] · rep 1.00 (n=15) · stress 1.00 (n=10) | 1843 ms | $0.0014 |

### Notes

- saturated: every regression case passes every trial on citations, context_precision, faithfulness, latency, mrr, no_pii, recall_at_4, refusal, safe_content; these graders can only catch regressions now. Add harder cases (or capability cases) to measure progress

### Top failing cases (up to 5)

- **assistant** `kb-q05` · context_recall: {"judges": [{"judge": "offline-judge-a", "model": "heuristic-judge-a", "reasoning": null, "value": 1.0}, {"judge": "offline-judge-b", "model": "heuristic-judge-
- **assistant** `kb-q06` · relevance: offline-judge-a: answer covers 40% of the question's terms | offline-judge-b: answer covers 40% of the question's terms
- **assistant** `kb-q23` · relevance: offline-judge-a: answer covers 33% of the question's terms | offline-judge-b: answer covers 33% of the question's terms

Adapters used: provider `mock` (fixture tested)
