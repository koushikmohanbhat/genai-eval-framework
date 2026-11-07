# DPDP-safe eval data

Golden sets built from real conversations, judge prompts and stored results are all personal data under India's
Digital Personal Data Protection Act. This page states exactly what is protected, where, and what is not.

## Data flow

| Step | What happens to personal data | Raw or tokenised |
|---|---|---|
| Dataset → target | the target (your app, agent or model) receives the case input as written | **raw**: the system under test must see real input |
| Target → provider | the target's own model and tool calls | **raw**, under the target's own provider agreements and logging |
| Deterministic graders, `pii_leak` | run in-process on the raw sample | raw, never sent anywhere |
| Sample → judge | input, output and context are tokenised before the judge call | **tokenised** |
| Provider-side records of judge calls | the provider may log prompts per its data-use terms | tokenised (only tokens leave the process) |
| Result JSON, reports, OTel spans, published metrics | samples (input, output, messages, context, trajectory, error strings, metadata) and scores (reasoning, details, errors, labels) are tokenised before they are written | **tokenised** (`suite.tokenize: true`, the default) |
| Response cache | **off by default** when tokenisation is on, because it would store raw outputs. If you opt in per target (`options: {cache: true}`), the cache directory is created `0700` and files `0600`, and the run warns | raw, local disk only |
| Online ingest | production samples go through the same tokenisation before grading by judges and before storage | tokenised |
| Python / HTTP graders and targets | your code receives what the framework passes it (raw for targets, raw for deterministic custom graders) | see [security boundary](architecture.md#security-boundary) |

## The tokeniser

The built-in tokeniser replaces Aadhaar, PAN, UPI ids, +91 phone numbers, email addresses and card numbers with
`<TYPE_hex12>` tokens: a keyed HMAC-SHA256, truncated. It is **one-way**: there is no token vault and tokens can't be
reversed by the framework.

- **Key.** Set `GENAI_EVALS_TOKEN_KEY` (from a secret manager, not the repo). Without it a random per-run key is used and
  the run warns: tokens are then not stable across runs, so baselines and references won't match. The result's
  `data_handling.tokenizer.stable_key` records which.
- **Scope.** Only the structured identifiers above are tokenised. Names, addresses, dates of birth, medical details and
  free-text identifiers pass through. Use the DPDP framework's cloud detectors, or redact upstream, for those.
- **When tokens hide meaning.** A judge sees `<PHONE_IN_3fa9…>`, not the number. Rubrics that depend on the value
  (does the phone number in the answer match the one on file?) must use a deterministic grader on raw values instead.
  The same value always maps to the same token within a key, so "is it the same number?" still works.
- **Keyed tokens are still personal data** if the key holder can recompute them. Treat stored results as pseudonymised,
  not anonymised.

## Rights and lineage

| Where | Control |
|---|---|
| Lineage | with `tokenizer: {type: dpdp_rights, config: dpdp.yaml}`, rows registered via `DPDPRightsTokenizer.register_eval_row` become `eval_row` artifacts in the [DPDP Data Rights Framework](https://github.com/koushikmohanbhat/dpdp-data-rights-framework), so an erasure request deletes them |
| Prompts | each prompt version declares `data_elements` and `purposes`; promotion to prod is blocked when a new one appears until it is acknowledged (`--ack-dpdp`) after the consent notice and ledger are updated |
| Datasets | build golden sets from tokenised production samples, or with the synthetic generator pattern in `examples/insurance/build_data.py` |
| Graders | `pii_leak` catches raw structured identifiers in outputs (Aadhaar with Verhoeff check, PAN, UPI, +91 phones, email, Luhn-valid card numbers). A pass does not mean the output contains no personal data |

Interpretations of the Act should be validated with counsel. This framework provides controls, not legal advice.
