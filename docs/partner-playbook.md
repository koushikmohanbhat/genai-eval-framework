# Partner playbook

## Offers

| Offer | Scope | Output |
|---|---|---|
| **Proof of concept** (3–5 days) | Day-1 workshop; `cases day0` review package; SME sign-off on day 2; `--profile quickstart` (3–5 core checks, one judge, one repeat) on the customer's system | a scorecard marked *directional*: worth pursuing or not. Not release evidence |
| **Eval sprint** (about 2 weeks) | workshop with SMEs to define "good"; build a 100–300 case golden set (tokenised or synthetic); baseline the current system on whatever cloud it runs on; compare 2–3 models across providers; readout | exec scorecard, engineer report, gates in the customer's repo |
| **Prompt and release governance** | move prompts into the repo, set up `prompts.lock` stages and gated promotion, wire the GitHub Action / CodeBuild / Cloud Build | every prompt or model change measured before it ships |
| **Production quality monitoring** | connect AgentCore / CloudWatch or Cloud Trace / BigQuery sources in the customer's account, schedule online evals, publish quality metrics and alarms | quality next to latency on the same dashboards |
| **Managed eval ops** | monthly review, golden-set refresh from production failures (`cases harvest` → SME review → `cases promote`), judge recalibration and judge-ladder checks, model-upgrade regression runs | a running quality programme |

Statement of work: [SOW template](sow-template.md) (the Day-1 workshop and the SME-reviewed cases are deliverables in
both shapes). Customer already on Promptfoo: [layer on top, don't replace](promptfoo-migration.md).

## Sprint plan

1. **Days 1–2:** use cases, success criteria and risk list. Pick graders and draft gates. With no eval set yet,
   `genai-evals cases day0` drafts 50–100 cases from the knowledge base (and production, if traced) and a review
   sheet for the SME by day 2. Hand the customer's security team [docs/permissions.md](permissions.md) on day 1 so
   the Terraform is approved by the time it's needed.
2. **Days 3–5:** golden set from tokenised production samples plus SME-written edge cases. Mark critical cases and tags (language, channel, risk).
3. **Days 6–7:** targets for the current system and 2–3 candidates (`providers.yaml`); first runs; calibrate judges on 50–100 human labels.
4. **Days 8–9:** fix flaky cases, tune thresholds with the business, set baselines, wire CI in two tiers
   (`pr-check` on pull requests, `release-gate` for sign-off).
5. **Day 10:** readout with the scorecard; hand over the runbook.

## Why it helps the OpenAI go-to-market

Partners can show OpenAI models against the incumbent on the customer's own data and cloud, with error bars and no
re-instrumentation. Teams already on Promptfoo keep it: the framework exports suites to Promptfoo and gates and
reports Promptfoo's results ([Promptfoo](promptfoo.md)), and its evals follow OpenAI's evaluation best practices
([mapping](openai-best-practices.md)). Customers still on the hosted Evals platform have a migration appendix.
