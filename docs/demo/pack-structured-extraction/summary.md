## ✅ genai-evals: `structured-extraction`

Run `run_f842d89ae8ea` · 21 cases × 1 targets × 1 repeats

### Gates

| Gate | Target | Result | Values |
|---|---|---|---|
| valid-json | extractor | ✅ pass | `mean(valid_json)`=100.0% |
| field-accuracy | extractor | ✅ pass | `mean(fields, split='representative')`=100.0%, `ci_low(fields, split='representative')`=100.0% |
| field-accuracy-under-stress | extractor | ✅ pass | `mean(fields, split='stress')`=100.0% |
| amounts | extractor | ✅ pass | `mean(amount_exact, split='representative')`=100.0% |
| critical-documents | extractor | ✅ pass | `critical_pass()`=yes |
| ocr-slice | extractor | ✅ pass | `mean(fields, tag='ocr')`=100.0% |
| cost-budget | extractor | ✅ pass | `mean(cost_usd)`=0.4% |
| latency-budget | extractor | ✅ pass | `p95(latency_ms)`=2,043 |
| gst-fields | extractor | ✅ pass | `mean(fields, tag='gst')`=100.0% |
| gstin-format | extractor | ✅ pass | `mean(gstin_format)`=100.0% |

### Scores (mean, 95% CI; representative and stress splits reported separately)

| Target | valid_json | fields | amount_exact | latency | gstin_format | p95 latency | cost / case |
|---|---|---|---|---|---|---|---|
| extractor | 1.00 [1.00, 1.00] · rep 1.00 (n=14) · stress 1.00 (n=7) | 1.00 [1.00, 1.00] · rep 1.00 (n=14) · stress 1.00 (n=7) | 1.00 [1.00, 1.00] · rep 1.00 (n=14) · stress 1.00 (n=7) | 1.00 [1.00, 1.00] · rep 1.00 (n=14) · stress 1.00 (n=7) | 1.00 [1.00, 1.00] | 2043 ms | $0.0042 |

### Notes

- saturated: every regression case passes every trial on amount_exact, fields, gstin_format, latency, valid_json; these graders can only catch regressions now. Add harder cases (or capability cases) to measure progress
