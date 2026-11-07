# Adding customer documents

Each line is one document as text, with the expected fields, validated against
[`schema/case.schema.json`](../schema/case.schema.json) when the suite loads. The system's output must match
[`schema/output.schema.json`](../schema/output.schema.json); edit both together when the customer's fields differ.

```json
{"id": "ap-0142", "input": "<document text: OCR output, email body or PDF text>", "tags": ["ocr", "inr"],
 "reference": {"fields": {"vendor_name": "...", "invoice_number": "...", "invoice_date": "2026-11-05", "due_date": null,
                          "total_amount": 49560.0, "currency": "INR", "po_number": null, "tax_id": null}}}
```

- **Every field is listed**, with `null` where the document has none, so "invented a value" is caught as well as
  "missed a value".
- **Normalisers** in `suite.yaml` decide what counts as equal: `date` (any common format to ISO), `amount` (commas,
  lakh / crore, tolerance), `upper_alnum` for ids, `name` for names in any order.
- **Tags** become slices: format (`clean`, `email`, `ocr`), currency, supplier, language, document type.
- **Critical**: high-value documents and documents that must be rejected (credit notes, quotes, duplicates).
- **How many.** 200–500 documents for a release decision: at least 30 per format you gate on, every supplier
  template the customer cares about, and 20+ documents that must be rejected.
- **Where they come from.** Real documents, with personal data tokenised before they enter the repository; key
  them twice (two people) and resolve differences before they become references.
- **Images and PDFs**: run OCR first and store the text, so the same text is evaluated every run. Evaluate OCR
  separately if it changes.
