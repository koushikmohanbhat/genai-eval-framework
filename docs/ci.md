# CI and the GitHub Action

## GitHub Action

```yaml
# .github/workflows/evals.yml (in the application repo)
name: evals
on: [pull_request]
permissions: {contents: read, pull-requests: write, id-token: write}
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4          # only if targets / judges use Bedrock
        with: {role-to-assume: "${{ vars.EVAL_ROLE_ARN }}", aws-region: "${{ vars.AWS_REGION }}"}
      - uses: koushikmohanbhat/genai-eval-framework@v0.3.0   # pin a release tag (or commit SHA)
        with:
          suite: evals/fnol_agent.yaml
          providers: evals/providers.yaml
          baseline: evals/baselines/main.json
          extras: "openai,aws,gcp"
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          AWS_REGION: ${{ vars.AWS_REGION }}
          BEDROCK_MODEL_ID: ${{ vars.BEDROCK_MODEL_ID }}     # every ${VAR} your YAML reads, from repository variables
```

Settings come from repository **variables** (`vars.*`) and **secrets** (`secrets.*`), never from the workflow file.
`genai-evals init --pack <pack>` writes a complete workflow to `ci/github-actions-evals.yml`.

The action installs the framework, checks prompts, runs the suite, uploads the report as an artifact, posts the
Markdown summary as a PR comment, and fails the job on a blocking gate that fails or is *invalid*
([gate outcomes](gates.md#gate-outcomes)). Until a baseline exists, leave `baseline:` out and set
`allow-missing-baseline: "true"` if the suite has `delta()` gates.

## Baselines

```bash
genai-evals run suite.yaml --out out/main --allow-missing-baseline
genai-evals baseline promote out/main/result.json --name main --out evals/baselines/main.json   # commit it
genai-evals run suite.yaml --baseline evals/baselines/main.json                                 # PRs compare against it
```

Baselines hold summaries with per-case values, but no raw samples. A `--baseline` path that doesn't exist is an error
(exit 2), so a typo can't silently turn regression gates off.

## Experiments in CI

For a model upgrade or several candidate prompts, run an experiment and post its comparison:

```bash
genai-evals experiment run evals/model-upgrade.yaml --out out/exp --fail-on-regression
gh pr comment "$PR" --body-file out/exp/comparison.md
```

Upload `out/exp` as a build artifact; commit `manifest.json` with the change it justifies. See
[experiments](experiments.md).

## AWS CodeBuild / Google Cloud Build

```yaml
# buildspec.yml
phases:
  install: {commands: ["pip install 'genai-eval-framework[aws,openai] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@<tag-or-sha>'"]}
  build: {commands: ["genai-evals prompts check", "genai-evals run evals/suite.yaml --baseline evals/baselines/main.json --out out --publish cloudwatch"]}
reports: {evals: {files: ["out/junit.xml"], file-format: JUNITXML}}
```

```yaml
# cloudbuild.yaml
steps:
  - name: python:3.12
    entrypoint: bash
    args: ["-c", "pip install 'genai-eval-framework[gcp] @ git+https://github.com/koushikmohanbhat/genai-eval-framework@<tag-or-sha>' && genai-evals run evals/suite.yaml --baseline evals/baselines/main.json --out out --publish cloud_monitoring --project $PROJECT_ID"]
```

## Scheduled online evals

`terraform/aws` creates an EventBridge rule and a Lambda / ECS task that runs `genai-evals online` with read-only
access. `terraform/gcp` creates a Cloud Scheduler job and a Cloud Run job. Scores land in CloudWatch or Cloud
Monitoring, next to the existing alarms.
