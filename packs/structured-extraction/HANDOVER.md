# Handover: Structured extraction

What the customer owns once the partner engagement ends. Fill in the owners, tick each item together with the
customer, and keep this file in the repository. `genai-evals doctor` reports unticked items and empty owners.

| Area | Owner (name / team) |
|---|---|
| Dataset and cases | Owner: ____ |
| Baselines and release gates | Owner: ____ |
| Secrets and access | Owner: ____ |
| CI pipeline | Owner: ____ |
| Online monitoring and alerts | Owner: ____ |

## Dataset
- [ ] The customer can add a case, run `genai-evals validate suite.yaml` and fix the errors it reports.
- [ ] Production failures are reviewed on a schedule (who, how often) and added as cases, tokenised, with a `lineage_ref`.
- [ ] Synthetic starter cases have been replaced or reviewed by an SME.

## Baselines and release gates
- [ ] `baselines/main.json` exists and the team knows when to refresh it (`genai-evals baseline promote` after an approved release).
- [ ] Gate thresholds are agreed with the product owner and the reasons are written next to them in `suite.yaml`.
- [ ] Critical cases are marked, and everyone knows a critical failure blocks a release regardless of averages.


## Secrets and access
- [ ] API keys live in the customer's secret manager or CI secrets, never in the repository; `.env` is not committed.
- [ ] `GENAI_EVALS_TOKEN_KEY` is set from a secret so tokens are stable across runs.
- [ ] Cloud access uses the read-only roles from `terraform/` (or equivalent) and is owned by the customer's platform team.

## CI gate
- [ ] The workflow runs on every pull request that touches prompts, retrieval, tools or models, and its failure blocks merge.
- [ ] The team knows the exit codes: 0 pass, 1 a blocking gate failed or was invalid, 2 configuration error.

## Online monitoring
- [ ] `genai-evals online` runs on a schedule in the customer's account with the sampling rate agreed.
- [ ] Alerts on the published scores go to the team that owns the system, with a runbook link.

## Adapters and validation
- [ ] Every adapter in use is recorded in `validation.yaml` as live_tested (and customer_validated once signed off).
- [ ] `genai-evals doctor` passes.

## Specific to this pack
- [ ] The output schema is a contract with downstream systems: changing it means a new prompt version, an updated schema and updated reference fields in the same pull request.
- [ ] New document layouts or suppliers seen in production are added as cases (tokenised) before the extractor is changed for them.

