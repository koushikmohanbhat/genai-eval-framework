# DPDP-safe eval data

Golden sets built from real conversations, judge prompts and stored results are all personal data under India's
Digital Personal Data Protection Act.

| Where | Control |
|---|---|
| Judges | case input, output and context are tokenised before any judge call |
| Results store, reports, OTel | samples are tokenised before they are written (`suite.tokenize: true`, the default) |
| Online ingest | the same tokenisation runs on production samples; the collector configs redact before export |
| Datasets | build golden sets from tokenised production samples, or with the synthetic generator pattern in `examples/insurance/build_data.py` |
| Lineage | with `tokenizer: {type: dpdp_rights, config: dpdp.yaml}`, rows registered via `DPDPRightsTokenizer.register_eval_row` become `eval_row` artifacts in the [DPDP Data Rights Framework](https://github.com/koushikmohanbhat/dpdp-data-rights-framework), so an erasure request deletes them |
| Prompts | each prompt version declares `data_elements` and `purposes`; promotion to prod is blocked when a new one appears until it is acknowledged (`--ack-dpdp`) after the consent notice and ledger are updated |
| Graders | `pii_leak` catches raw identifiers in outputs (Aadhaar with Verhoeff check, PAN, UPI, +91 phones, email, Luhn-valid card numbers) |

The built-in tokeniser is keyed and deterministic: set `GENAI_EVALS_TOKEN_KEY` so that tokens are stable across runs and
references still match outputs. Graders that must see raw values (such as `pii_leak`) run before tokenisation and
never send raw values anywhere.

Interpretations of the Act should be validated with counsel. This framework provides controls, not legal advice.
