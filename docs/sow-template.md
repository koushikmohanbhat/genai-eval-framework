# Statement of work template: eval engagement

A starting point for the eval part of a partner SOW. Copy it, fill in the brackets, and have the partner's legal team
adapt the commercial terms; nothing here is legal advice. Two shapes: a **proof of concept** (3–5 days) and an
**eval sprint** (about two weeks). Both start with the same Day-1 workshop, because agreeing what "good" means with
the people who own the answers is what makes later numbers believable.

## 1. Scope

[Customer] engages [Partner] to evaluate [system: e.g. the claims-intake assistant] using the GenAI Eval Framework,
pack `[rag-knowledge-assistant | tool-agent | structured-extraction]`[, overlays `[financial-services, security-guardrails]`].

| | Proof of concept | Eval sprint |
|---|---|---|
| Duration | 3–5 working days | about 10 working days |
| Run profile | `quickstart`: 3–5 core checks, one judge, one repeat, up to 30 cases | `pr-check` and `release-gate` |
| What the results support | a directional view: is this worth pursuing? | a release decision, with calibrated judges and a baseline |
| Not included | judge calibration, statistical sign-off, CI integration | live telemetry monitoring (optional add-on) |

## 2. Day-1 workshop (mandatory in both shapes)

Attendees: the business owner of [system], at least one subject-matter expert (SME) who can judge answers, the
customer's technical lead, and the partner's eval lead. Half a day.

1. Agree the three to five outcomes that matter (for example: grounded answers, correct refusals, no personal data in
   answers) and which ones would block a release.
2. Agree the risk list: what must never happen (critical cases).
3. Agree the data the evaluation may use (section 4).
4. Agree who reviews the draft cases and by when.

**Deliverable D1, review package (end of day 1):** draft test cases and an SME review sheet, produced with
`genai-evals cases day0` from the documents the customer approved (and, if production traces exist, a tokenised
sample of real questions). If the customer already has an eval set or Promptfoo tests, D1 is a review sheet of those
instead; seeding isn't needed.

**Deliverable D2, signed-off cases (day 2 for a POC, day 3 for a sprint):** the SME's decisions on every draft
(keep, fix or drop, with their name), promoted into the eval set. No case counts until its row is signed.

**Customer dependency:** an SME available for about two hours on day 2. If the review slips, the timeline slips by
the same amount.

## 3. Deliverables

| # | Deliverable | POC | Sprint |
|---|---|---|---|
| D1 | Review package: draft cases and SME review sheet | day 1 | day 1 |
| D2 | Signed-off eval set | day 2 | day 3 |
| D3 | Run report and one-page scorecard for [system] | day 3–5, marked *directional* | day 7 |
| D4 | Judge calibration: [30+] human labels per judge-graded check, agreement with confidence interval | – | day 6 |
| D5 | Comparison of up to [3] options (model, prompt or retriever) on the same cases | optional | day 8 |
| D6 | Release gates in the customer's CI (`pr-check` on pull requests, `release-gate` before release) | – | day 9 |
| D7 | Handover: runbook, handover checklist with named customer owners, `genai-evals doctor` passing | – | day 10 |

## 4. Data handling

- [Customer] approves which documents and traces the evaluation may read, before day 1.
- **Documents sent to a model.** Drafting cases sends document passages to the generator model [profile: e.g. an
  in-region Bedrock / Vertex model]. Judges receive tokenised text. [Customer] approves the model and region.
- Structured identifiers ([sets: e.g. global, in, pci]) are tokenised before judges, results and reports. Free text
  (names, addresses) is not detected by default; [state any extra redaction].
- Eval data stays in [customer account / repository]; [Partner] keeps no copy after the engagement.
- Personal data in production-derived cases is registered for erasure ([DPDP Data Rights Framework, if used]).
- [Optional] [Customer] agrees that [Partner] may share an anonymised usage summary (pack, versions, gate outcome
  counts; no customer data, names hashed) with [partner programme], as described in the framework's
  [usage export](usage-export.md). [Partner] shares the file with [customer role] before sending it.

## 5. Assumptions

- Access to [system] through [endpoint / account] by day 2, with test credentials.
- For a POC, results are directional: one judge without calibration, one repeat, a small sample. They are not
  evidence for a release decision.
- Security cases are a starter set ([n] per threat); a security sign-off needs cases on the customer's own data and
  an application security review.

## 6. Acceptance

Each deliverable is accepted when [customer role] confirms it in writing, or [5] working days after delivery without
objection. D2 is accepted only with the SME's name on every decided row.
