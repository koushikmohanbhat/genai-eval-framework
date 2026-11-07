# Adapters: contracts, plugins and conformance

Everything cloud- or vendor-specific lives in an adapter. Suites, results and reports only ever see cloud-neutral
shapes (`Sample`, `Score`, the [result JSON](results.md)), so a new adapter never changes them, and a suite written
for one customer's stack runs unchanged on another's.

## The six contracts (contract version 1)

| Kind | Base class | What it must do | Selected by |
|---|---|---|---|
| provider | `genai_evals.providers.base.Provider` | `_complete(messages, tools, **opts) -> ProviderResponse` (text, tool calls with dict args, usage, model id); optional `_embed`. Retries, rate limits and pricing come from the base class | `provider:` in a profile |
| source | `genai_evals.sources.base.Source` | set `kind` (`spans`, `records`, `history`, `metrics`, `scores`) and implement `fetch()` for the live path; the base class reads fixtures, samples and normalises spans with the OTel GenAI conventions | `type:` in `sources.yaml` |
| target | `genai_evals.targets.base.Target` | `run(case, repeat) -> Sample` (agent frameworks) | `framework:` on an `agent` target |
| grader | `genai_evals.graders.base.Grader` | `grade(sample, case) -> Score` with a value in 0..1 (or `skipped`) | `type:` in a grader |
| store | `genai_evals.plugins.Store` | `save(result)`, `history(suite, limit)` | URL scheme of `--store` |
| report | `genai_evals.plugins.ReportWriter` | `render(result) -> str | bytes`, `name`, `filename` | `result export --format` |

Rules that keep the formats cloud-neutral:

- Map provider- and cloud-specific fields onto `Sample` fields (`output`, `messages`, `context`, `trajectory`,
  `usage`, `latency_ms`, `trace_id`, `session_id`, `prompt_versions`, `metadata.start_ns`); put anything else in
  `metadata`, never in new top-level fields.
- Make API calls through `self.call(fn, ...)` (sources) or the base class's `complete()` (providers), so every
  adapter retries and backs off the same way.
- Skip a malformed record instead of raising; a source must never crash on missing fields.
- Import SDKs inside methods, and only when no client was injected, so an adapter can be tested without its SDK.

## Writing a plugin

A plugin is an ordinary Python package; the framework finds it through entry points, so nobody forks this repo.

```toml
# pyproject.toml of acme-evals
[project.entry-points."genai_evals.sources"]
azure_monitor = "acme_evals.azure:AzureMonitorSource"
[project.entry-points."genai_evals.providers"]
azure_openai = "acme_evals.azure:AzureOpenAIProvider"
```

```python
class AzureMonitorSource(Source):
    kind = "spans"
    contract_version = "1"            # refused if the framework's major contract version differs
    cloud = "azure"
    maturity = "fixture_tested"       # what your own conformance run proves; never more than that

    def fetch(self):
        client = self._client or make_client(self.opt("workspace_id"))
        rows, token = [], None
        while True:
            page = self.call(client.query, self.spec.filter, page_token=token)    # retries come from the base class
            rows += page["rows"]
            token = page.get("next")
            if not token:
                return rows
```

Groups: `genai_evals.providers`, `genai_evals.sources`, `genai_evals.targets`, `genai_evals.graders`,
`genai_evals.stores`, `genai_evals.reports`. A plugin can't replace a built-in of the same name.
`genai-evals adapters` lists everything installed with its origin and status.

## Conformance

Every adapter, built-in or plugin, must pass the same checks before it can be called fixture tested.

| Check | Sources | Providers |
|---|---|---|
| formats | the fixture as JSONL, JSON array and gzip gives identical output | |
| normalisation | output has the neutral shape for its kind (Samples with case id, target and origin; join records with `time_ns`; history with role and content; scores in 0..1); expected fields are populated | text, tool calls with dict arguments, non-negative usage, model id, latency |
| deterministic | same output on a second read and with records shuffled | |
| missing_fields | records with random fields removed never crash the adapter | |
| empty | no records gives empty output | |
| timestamps | span start times normalised to epoch nanoseconds | |
| pagination | the live path follows page tokens and matches the fixture output | |
| retries | a throttled first call is retried | a transient error is retried; a client error is not |
| tool_calls / json_mode | | tool calls parsed; JSON mode accepted |

Pagination and retries need a fake client in the spec; without one they are reported as **not covered**, which is
never counted as a pass.

```python
# acme_evals/conformance.py
from genai_evals.conformance import SourceConformance

SPECS = [SourceConformance("azure_monitor", "tests/fixtures/azure_monitor.jsonl", {"workspace_id": "w"},
                           expect_fields=["output", "trajectory", "session_id"], client_factory=FakeAzureClient)]
```

```bash
genai-evals conformance --spec acme_evals.conformance:SPECS
```

or in pytest: `genai_evals.conformance.assert_conformant(spec)`. `client_factory(records, page_size, failures)`
returns a fake client that serves `records` in pages of `page_size` and raises a throttling error `failures` times
first. The built-in specs in `genai_evals/conformance/builtin.py` are worked examples.

## Maturity: fixture tested, live tested, customer validated

| Level | Meaning | Who sets it |
|---|---|---|
| implemented | written to the documented API; no conformance or recorded-response tests | the catalog |
| fixture tested | passes the conformance kit in CI | the catalog (CI fails if the claim isn't backed by a spec) |
| live tested | run against a live service | a record in the project's `validation.yaml` |
| customer validated | validated in the customer's environment and signed off | a record in `validation.yaml` |

```bash
genai-evals adapters record --kind source --name aws_agentcore_observability --status live_tested \
    --environment "customer sandbox, ap-south-1" --evidence out/online/result.json --by "partner name"
```

Built-ins never claim more than fixture tested. Every result lists the adapters it used with their effective status
(the built-in level raised by the project's records), and the HTML report and Markdown summary show it, so a reader
always knows which numbers came through a proven integration. The current catalog is in [adapters](adapters.md).
