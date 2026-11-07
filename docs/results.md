# Results: a portable, versioned format

Every run (offline, online, experiment variant or import) produces one JSON result. It is the only interface between
the framework and anything that stores, compares or renders results, so nothing downstream needs this framework, a
particular cloud database or a particular CI system.

## Versioning

```json
{"schema": "genai-evals/result", "schema_version": "1.0", "framework_version": "0.3.0", "run_id": "...", ...}
```

- **Minor versions only add optional fields**, so any 1.x reader reads any 1.x result.
- **A newer major version is refused** with a message to upgrade, never misread.
- **Results from before versioning** (framework 0.2 and earlier) are upgraded in memory when loaded
  (`upgraded_from: "0"`), so old baselines keep working.

The JSON Schema ships with the package:

```bash
genai-evals result schema > result-1.0.schema.json
genai-evals result validate out/run/result.json
```

## What a result holds

| Field | Contents |
|---|---|
| `run_id`, `suite`, `created_at`, `git_sha`, `mode`, `pack` | what ran, when, from which commit, offline / online / import |
| `passed`, `gates` | the verdict and every gate with `status` pass / fail / invalid, severity and message |
| `summary` | per target: grader means with CIs, `per_case` values, pass^k, slices by tag, ops (cost, latency, tokens, errors), critical cases |
| `cases`, `samples`, `scores` | the inputs (tokenised), what each target produced, and every grader's score |
| `graders`, `judge_provenance`, `calibration` | grader config hashes, judge models and prompt hash, calibration with κ intervals |
| `adapters` | every adapter used, with its effective status (fixture tested / live tested / customer validated) |
| `report` | the report template (title, intro, headline grader, failure categories) |
| `data_handling` | tokeniser, `on_error`, `min_n` |
| `comparisons`, `baseline` | paired comparison with the baseline run, when there is one |
| `sources` | online runs: per source, graded vs read counts, sampling, window, whether a fixture was used |

## Exports

```bash
genai-evals result export out/run/result.json --format csv,jsonl,promptfoo --out out/export
```

| Format | File | For |
|---|---|---|
| `csv` | `scores.csv` | spreadsheets and BI tools: one row per score with target, case, repeat, grader, value, pass, tags, critical, prompt versions, latency, cost |
| `jsonl` | `scores.jsonl` | the same rows for data pipelines (BigQuery, Athena, DuckDB, pandas) |
| `promptfoo` | `promptfoo-results.json` | Promptfoo's results shape, to view next to Promptfoo runs; round-trips with `genai-evals promptfoo import` |
| `html`, `scorecard`, `markdown`, `junit`, `json` | the usual reports | re-rendering a stored result |

More formats are [report plugins](plugins.md).

## Stores

| `--store` URL | Store |
|---|---|
| `file://results/` | a folder of result files plus `index.jsonl`; no database at all |
| `sqlite:///.genai-evals/results.db` | a local SQLite file |
| `postgresql+psycopg://...` | any Postgres (RDS, Aurora, Cloud SQL, AlloyDB, on-prem) |
| `<scheme>://...` | a [store plugin](plugins.md) |

Baselines (`genai-evals baseline promote`) are trimmed results (summaries and per-case values, no samples) and are
plain JSON files you commit or keep anywhere. `genai-evals compare current.json baseline.json` works on any two
results, wherever they were stored.
