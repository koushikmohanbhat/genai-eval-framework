# Adding customer cases

Each line of a dataset file is one question, validated against [`schema/case.schema.json`](../schema/case.schema.json)
when the suite loads. A row that doesn't match stops the run with the case id and the field at fault.

```json
{"id": "hr-017", "input": "How many days of annual leave do I get?", "tags": ["answerable", "leave"],
 "reference": {"answerable": true, "gold_passages": ["<id your retriever returns>"], "answer": "24 days per year"}}
{"id": "oos-004", "input": "What is our share price?", "tags": ["out-of-scope"], "reference": {"answerable": false}}
```

- **Answerable questions** need `gold_passages` (the ids your retriever returns for the passages that answer them)
  and a short `answer`. Tag them `answerable`; the retrieval, faithfulness, relevance and citation graders run only on
  these.
- **Unanswerable questions** (`answerable: false`) must be refused: out-of-scope questions, requests for secrets,
  other people's personal data, prompt injection. Mark the ones that must never fail `critical: true`.
- **Tags** become slices in every report. Use topic, language (`hi`, `hinglish`), channel and risk tags.
- **How many.** 100–300 questions for a release decision: at least 30 per slice you want to gate on, 20+
  unanswerable, and every safety scenario the customer cares about. The 20 starter cases show the shape only.
- **Where cases come from.** Subject-matter experts write the first 50. Then add real questions from search logs or
  production sessions (`genai-evals online` failures), tokenised first ([DPDP](../../../docs/dpdp.md)), with
  `source: production` and a `lineage_ref` so an erasure request can find them.
- **Keep starter cases?** Replace them. They describe a fictional company.

Use a separate file per source (`data/sme.jsonl`, `data/production.jsonl`) and list them in the suite:
`dataset: data/sme.jsonl` plus `extra_datasets: [data/production.jsonl]`.
