# Partner playbook

## Offers

| Offer | Scope | Output |
|---|---|---|
| **Eval sprint** (about 2 weeks) | workshop with SMEs to define "good"; build a 100–300 case golden set (tokenised or synthetic); baseline the current system on whatever cloud it runs on; compare 2–3 models across providers; readout | exec scorecard, engineer report, gates in the customer's repo |
| **Prompt and release governance** | move prompts into the repo, set up `prompts.lock` stages and gated promotion, wire the GitHub Action / CodeBuild / Cloud Build | every prompt or model change measured before it ships |
| **Production quality monitoring** | connect AgentCore / CloudWatch or Cloud Trace / BigQuery sources in the customer's account, schedule online evals, publish quality metrics and alarms | quality next to latency on the same dashboards |
| **Managed eval ops** | monthly review, golden-set refresh from production failures, judge recalibration, model-upgrade regression runs | a running quality programme |

## Sprint plan

1. **Days 1–2:** use cases, success criteria and risk list. Pick graders and draft gates.
2. **Days 3–5:** golden set from tokenised production samples plus SME-written edge cases. Mark critical cases and tags (language, channel, risk).
3. **Days 6–7:** targets for the current system and 2–3 candidates (`providers.yaml`); first runs; calibrate judges on 50–100 human labels.
4. **Days 8–9:** fix flaky cases, tune thresholds with the business, set baselines, wire CI.
5. **Day 10:** readout with the scorecard; hand over the runbook.

## Why it helps the OpenAI go-to-market

Partners can show OpenAI models against the incumbent on the customer's own data and cloud, with error bars and no
re-instrumentation. Customers moving off the hosted Evals platform and prompt objects get a code-first landing place
that also exports to Promptfoo.
