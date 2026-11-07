## ❌ genai-evals: `claims-extraction`

Run `run_3cde466d6741` · 24 cases × 2 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| field-accuracy | extractor-v1 | ❌ fail | `mean(fields)`=76.2%, `ci_low(fields)`=65.5% |
| field-accuracy | extractor-v2 | ✅ pass | `mean(fields)`=100.0%, `ci_low(fields)`=100.0% |
| valid-json | extractor-v1 | ✅ pass | `mean(schema)`=100.0% |
| valid-json | extractor-v2 | ✅ pass | `mean(schema)`=100.0% |
| hinglish-slice | extractor-v1 | ❌ fail | `mean(fields, tag='lang:hinglish')`=57.1% |
| hinglish-slice | extractor-v2 | ✅ pass | `mean(fields, tag='lang:hinglish')`=100.0% |
| high-value-claims | extractor-v1 | ❌ fail | `critical_pass()`=no |
| high-value-claims | extractor-v2 | ✅ pass | `critical_pass()`=yes |
| latency-budget | extractor-v1 | ✅ pass | `p95(latency_ms)`=1,068 |
| latency-budget | extractor-v2 | ✅ pass | `p95(latency_ms)`=1,584 |

### Scores (mean, 95% CI)

| Target | schema | fields | latency | p95 latency | cost / case |
|---|---|---|---|---|---|
| extractor-v1 | 1.00 [1.00, 1.00] | 0.76 [0.65, 0.86] | 1.00 [1.00, 1.00] | 1068 ms | $0.0009 |
| extractor-v2 | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1584 ms | $0.0016 |

### Top failing cases (up to 5)

- **extractor-v1** `ex-03` · fields: 3/6 fields correct
- **extractor-v1** `ex-11` · fields: 3/6 fields correct
- **extractor-v1** `ex-15` · fields: 3/6 fields correct
- **extractor-v1** `ex-23` · fields: 3/6 fields correct
- **extractor-v1** `ex-04` · fields: 4/6 fields correct
