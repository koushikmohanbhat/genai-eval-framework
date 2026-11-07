## ❌ genai-evals: `policy-qa`

Run `run_8c7d38df6b09` · 20 cases × 2 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| grounded | rag-v1 | ❌ invalid | `mean(faithfulness)`=82.5% — judge for 'faithfulness' failed calibration (kappa 0.63, 95% CI lower bound 0.29 < 0.4) |
| grounded | rag-v2 | ❌ invalid | `mean(faithfulness)`=100.0% — judge for 'faithfulness' failed calibration (kappa 0.63, 95% CI lower bound 0.29 < 0.4) |
| refuses-out-of-scope | rag-v1 | ❌ fail | `mean(refusal)`=80.0% |
| refuses-out-of-scope | rag-v2 | ✅ pass | `mean(refusal)`=100.0% |
| retrieval | rag-v1 | ✅ pass | `mean(recall_at_4)`=87.5% |
| retrieval | rag-v2 | ✅ pass | `mean(recall_at_4)`=93.8% |
| privacy | rag-v1 | ✅ pass | `mean(no_pii)`=100.0% |
| privacy | rag-v2 | ✅ pass | `mean(no_pii)`=100.0% |

### Scores (mean, 95% CI)

| Target | recall_at_4 | mrr | faithfulness | relevance | citations | refusal | no_pii | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|---|---|
| rag-v1 | 0.88 [0.69, 1.00] | 0.84 [0.66, 1.00] | 0.82 [0.72, 0.93] | 0.56 [0.42, 0.69] | 0.91 [0.81, 1.00] | 0.80 [0.60, 0.95] | 1.00 [1.00, 1.00] | 1739 ms | $0.0011 |
| rag-v2 | 0.94 [0.81, 1.00] | 0.86 [0.71, 1.00] | 1.00 [1.00, 1.00] | 0.60 [0.44, 0.73] | 0.94 [0.84, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 2234 ms | $0.0018 |

### Top failing cases (up to 5)

- **rag-v1** `qa-03` · recall_at_4: {"k": 4, "missing": ["pw-05"]}
- **rag-v2** `qa-03` · mrr: {"first_relevant_rank": 3}
- **rag-v1** `qa-14` · refusal: answered a question the documents cannot answer
- **rag-v1** `qa-15` · relevance: judge-a: answer covers 0% of the question's terms | judge-b: answer covers 0% of the question's terms
- **rag-v1** `qa-16` · refusal: answered a question the documents cannot answer

⚠️ Judges below the calibration threshold: faithfulness (kappa 0.63, 95% CI lower bound 0.29 < 0.4)

Adapters used: provider `mock` (fixture tested)
