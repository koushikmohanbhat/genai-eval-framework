## ❌ genai-evals: `claims-extraction`

Run `run_995931603235` · 24 cases × 2 targets × 1 repeats

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

### Scores (mean, 95% CI; representative and stress splits reported separately)

| Target | schema | fields | latency | p95 latency | cost / case |
|---|---|---|---|---|---|
| extractor-v1 | 1.00 [1.00, 1.00] | 0.76 [0.65, 0.86] | 1.00 [1.00, 1.00] | 1068 ms | $0.0009 |
| extractor-v2 | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1584 ms | $0.0016 |

### Notes

- fields @ extractor-v1: 95% interval is ±10 points on 24 case(s), so differences smaller than that can't be told from noise; about 279 cases would give ±5 (genai-evals power)
- saturated: every regression case passes every trial on latency, schema; these graders can only catch regressions now. Add harder cases (or capability cases) to measure progress

### Top failing cases (up to 5)

- **extractor-v1** `ex-03` · fields: 3/6 fields correct
- **extractor-v1** `ex-11` · fields: 3/6 fields correct
- **extractor-v1** `ex-15` · fields: 3/6 fields correct
- **extractor-v1** `ex-23` · fields: 3/6 fields correct
- **extractor-v1** `ex-04` · fields: 4/6 fields correct
