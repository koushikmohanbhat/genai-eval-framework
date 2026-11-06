# Prompts as code

Prompts, system instructions, **tool descriptions**, few-shot examples and model hints live in the application repo.
Each version is an immutable folder. `prompts.lock` records content hashes and which version each stage uses.

```
prompts/
  claims_agent/
    1.3.2/  prompt.yaml  system.md.j2  tools.yaml  examples.jsonl
    1.4.0/  ...
prompts.lock
```

```yaml
# prompts/claims_agent/1.4.0/prompt.yaml
id: claims_agent
version: 1.4.0                   # semver: major = behaviour/contract, minor = capability, patch = wording
variables: [language]
model_hints: {temperature: 0.2}
output_schema: claim.schema.json # optional, relative to the version folder
data_elements: [NAME, PHONE_IN, POLICY_NO, FREE_TEXT, HEALTH_INFO]
purposes: [claims_processing, injury_assistance]
owner: claims-ai-team
changelog: "Escalate injury cases to a human"
```

## Lifecycle

```bash
cp -r prompts/claims_agent/1.3.2 prompts/claims_agent/1.4.0   # edit the copy, bump version in prompt.yaml
genai-evals prompts lock                                      # record the hash (refuses if a locked version was edited)
genai-evals prompts diff claims_agent@1.3.2 claims_agent@1.4.0 --suite fnol_agent.yaml   # file diff + gated A/B
genai-evals prompts promote claims_agent 1.4.0 --stage staging
genai-evals prompts promote claims_agent 1.4.0 --stage prod   # blocked if new data elements / purposes (DPDP)
genai-evals prompts check                                     # CI: lock integrity + DPDP warnings (--strict-dpdp to fail)
```

Rolling back means pointing `prod` at the previous version again: a one-line change to `prompts.lock`, made in a pull request.

## Runtime

```python
from genai_evals.prompts import load
p = load("claims_agent", stage="prod", variables={"language": "hi"})   # GENAI_EVALS_PROMPTS_DIR, GENAI_EVALS_PROMPT_STAGE
p.system, p.tools, p.examples, p.model_hints, p.version, p.hash
```

`load()` stamps `prompt.id`, `prompt.version` and `prompt.hash` on the current OpenTelemetry span. Production traces
in CloudWatch / Cloud Trace then say which version produced each answer, and online scores are split by version (as
tag `prompt:<id>@<version>`).

## Registry mirror (optional)

```bash
genai-evals prompts push claims_agent --to bedrock --region ap-south-1            # dry run prints the payload
genai-evals prompts push claims_agent --to bedrock --region ap-south-1 --apply    # CreatePrompt / CreatePromptVersion
genai-evals prompts push claims_agent --to langfuse --apply
genai-evals prompts push claims_agent --to vertex --project p --location l --apply   # experimental (preview SDK)
```

The repo stays the source of truth, and the registry is a mirror.

## In suites

`prompt: claims_agent` uses the version the target's `prompt_stage` (default `prod`) points at, while
`prompt: claims_agent@1.4.0` pins a version. `genai-evals run --prompt claims_agent=1.4.0` overrides it for one run.
Prompt hashes are part of the cache key.
