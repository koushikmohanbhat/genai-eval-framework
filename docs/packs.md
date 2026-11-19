# Eval packs

An eval pack is everything a partner needs to evaluate one workflow, for any customer, on any stack. A pack is a
**workflow pattern**, not a customer framework: the customer supplies their cases and picks the adapters for their
stack, and the pack stays the same. Industry specifics go in optional [overlays](#overlays).

```bash
genai-evals pack list
genai-evals pack info tool-agent           # targets, graders, failure categories, gates, adapter status, overlays
genai-evals init my-eval --pack tool-agent # a runnable project, already run once offline
```

## The packs

| Pack | Workflow | Graders | Failure categories | Overlays |
|---|---|---|---|---|
| [`rag-knowledge-assistant`](../packs/rag-knowledge-assistant) | questions answered from documents | recall@4, MRR, faithfulness, relevance, citations, refusal, PII, safety policy, latency | retrieval miss, unsupported claim, missing or wrong citation, wrong refusal, off-topic answer, privacy or safety, too slow | `insurance-policy`, `security-guardrails` |
| [`tool-agent`](../packs/tool-agent) | an agent acting on systems through tools | final state, tool arguments, call order, forbidden calls, escalation, step budget, promises policy, PII, latency (3 repeats, pass^k) | wrong outcome, wrong tool or arguments, policy violation, missing escalation, inefficient, privacy, too slow | `security-guardrails` |
| [`structured-extraction`](../packs/structured-extraction) | documents to JSON | output schema, field match with normalisers, amount and currency, latency | invalid output, wrong or missing field, wrong amount or currency, too slow | `india-gst` |

Each pack runs offline in CI: `genai-evals pack validate` checks its files, manifest, dataset, suite, generated docs
and overlays, then runs it offline and runs its monitoring checks on the recorded telemetry.

## Anatomy

| File | What it is | The partner changes it? |
|---|---|---|
| `pack.yaml` | manifest: inputs, targets, graders, failure categories, gates with reasons, compatibility, validation status, overlays, handover items | no (pack authors only) |
| `suite.yaml` | graders, gates, `report:` template (title, intro, headline grader, failure categories) | thresholds, after the workshop |
| `schema/case.schema.json` | the dataset contract; every row is validated on load | only to add customer fields |
| `data/starter.jsonl`, `data/README.md` | synthetic starter cases; how to add real ones | replaced by customer cases |
| `targets/*.yaml` | one file per way of reaching the system, selected with `EVAL_TARGETS` | picks one, fills its variables |
| `providers.yaml` / `providers.live.yaml` | offline mock judges / live profiles from `${VAR}` | keeps the profiles in use |
| `sources.yaml`, `online_checks.yaml`, `fixtures/` | production monitoring, with recorded OTLP telemetry for offline runs | keeps one source, removes `fixture:` |
| `experiment.yaml` | a baseline and candidate changes, compared side by side | describes real candidates |
| `prompts/`, `prompts.lock` | versioned prompt for model-backed targets | new versions |
| `labels/` | judge calibration guidance and label template | SME labels |
| `RUNBOOK.md`, `HANDOVER.md` | generated from `pack.yaml`: the engagement steps; what the customer owns | ticks and owners |
| `validation.yaml` | live / customer validation records for adapters | `genai-evals adapters record` |
| `sim_system.py` (+ `tools.py`) | the offline simulator (and mock tools) | nothing; it stops being used |

`init` adds `.env.example`, `.gitignore`, `ci/github-actions-evals.yml`, `baselines/` and the live providers file.

### Choosing the system with `targets_from`

A pack's suite reads its targets from a file named by an environment variable:

```yaml
targets_from: "${EVAL_TARGETS:-targets/offline.yaml}"
```

so moving from the simulator to `targets/http.yaml`, `targets/bedrock-kb.yaml`, `targets/agentcore.yaml` or any
other file is one line in `.env`. Gates, graders and reports don't change, and two projects for two customers on
different clouds differ only in `.env` and their data.

### Report template

```yaml
report:
  title: Knowledge assistant quality
  intro: Does the assistant find the right documents, answer only from them ...
  headline: faithfulness                 # the grader on the scorecard cards and quality-cost chart
  failure_categories:                    # category -> graders; the report counts failing cases per category
    retrieval miss: [recall_at_4, mrr]
    unsupported claim: [faithfulness]
```

The engineer report gets a failure-category table per target, the scorecard uses the title, intro and headline,
and the template travels in the result JSON, so any renderer can use it.

## Overlays

An overlay adds industry-specific cases, graders, gates and failure-category entries to a pack without forking it:

```yaml
# overlays/india-gst/overlay.yaml
name: india-gst
cases: [cases.jsonl]                    # appended to the dataset (validated against the same schema)
graders:
  - {name: gstin_format, type: regex, pattern: '...', tags: [gst]}
gates:
  - {name: gst-fields, rule: "mean(fields, tag='gst') >= 0.95"}
failure_categories:
  wrong or missing field: [gstin_format]
```

An overlay can also switch on identifier detectors (`pii: {sets: [...], custom: [...]}`, added to the suite's;
[detector sets](dpdp.md#detector-sets)).

**Domain overlays** keep the core domain-agnostic:

| Overlay | Pack | Adds |
|---|---|---|
| `insurance-policy` | rag-knowledge-assistant | motor policy wording cases, a no-payout-promise policy |
| `financial-services` | rag-knowledge-assistant | investment product cases; a mandatory risk disclaimer (`regex`), no personal investment advice and no guaranteed-return claims (`policy` judges); IBAN, card and folio numbers as personal data. The offline system has a `noncompliant: true` option that fails all three, to prove the overlay catches it. Replace the disclaimer text and the advice rule with the customer compliance team's wording |
| `india-gst` | structured-extraction | GSTIN and GST fields |
| `security-guardrails` | all three packs | OWASP LLM01 / LLM06 / LLM07 for RAG and agents; LLM01 / LLM02 / LLM07 for documents ([security](security.md)) |

Healthcare, retail and public-sector overlays follow the same shape; the `us_hipaa` and `uk` detector sets are
already in the core for them.

### Partner packs and overlays (your IP, outside the core)

A partner's vertical packs and overlays don't have to live in this repository. Ship them as a Python package that
registers them with an entry point, in a private repository or index:

```toml
[project.entry-points."genai_evals.overlays"]
sap-order-tools = "acme_evals_sap:sap_order_tools"   # module:folder with overlay.yaml, whose `pack:` names the pack
[project.entry-points."genai_evals.packs"]
banking-kyc = "acme_evals.banking_kyc"               # a folder with pack.yaml
```

After `pip install`, `genai-evals pack list` shows them marked `[partner]`, and `init --pack` / `--overlay` use them
by name. Built-in names can't be shadowed, and a package that fails to load only prints a warning. An unpackaged
overlay works too: `--overlay ./path/to/folder`. [`examples/partner-pack-sap`](../examples/partner-pack-sap) is a
complete example: SAP sales-order tool use for the `tool-agent` pack (credit memos over the approval limit, ship-to
changes after goods issue, another sold-to party's order, instructions planted in header texts), passing on the
guarded offline agent and failing on the vulnerable one.

Enable one with `genai-evals init ... --overlay india-gst`, or in an existing project with
`overlays: [overlays/india-gst]` in `suite.yaml`. Overlays only add; a grader name that clashes with the base suite
is an error. Overlay cases must be tagged so the gates can address their slice, and a slice needs `min_n` cases.

## Authoring a pack

1. Copy the closest pack, rename it in `pack.yaml` and `suite.yaml`.
2. Write the case schema first, then 15–20 synthetic starter cases covering every failure category and every
   critical scenario.
3. Write `sim_system.py` so that the starter cases pass every blocking gate offline, with options that simulate
   the regressions your `experiment.yaml` shows.
4. Add one target file per supported way of reaching a system, using only `${VAR}` for environment values.
5. Fill `pack.yaml`, then generate the runbook and handover: `genai-evals pack docs packs/<name>`.
6. Record telemetry for the monitoring demo: add a builder to `scripts/build_pack_fixtures.py`.
7. `genai-evals pack validate packs/<name>` must pass; the test suite runs it for every pack.

Packs are versioned (`version:` in `pack.yaml`) separately from the framework; `framework:` states the framework
versions a pack needs.
