#!/usr/bin/env bash
# A live failure catch on real infrastructure, for the 5-minute recording (docs/live-walkthrough.md).
# A real model on Bedrock or Vertex runs the tool-agent pack's support prompt against the pack's mock order tools
# (no deployed agent, no Terraform). Prompt 1.1.0 "simplifies" the refund rule and loses the 5,000-rupee limit; the
# release gate catches it.
#
#   CLOUD=aws  AWS_REGION=ap-south-1 BEDROCK_MODEL_ID=<model or inference profile> ./walkthrough.sh
#   CLOUD=gcp  GCP_PROJECT=<project> GCP_LOCATION=<region> VERTEX_MODEL=<model> ./walkthrough.sh
#   STEP=dry ./walkthrough.sh      # stop after the cost estimate
#
# Credentials: the standard chain (aws configure / SSO, gcloud auth application-default login). Cost: see the
# --dry-run line; the run is capped by BUDGET_USD (default 5).
set -euo pipefail
CLOUD="${CLOUD:?set CLOUD=aws or CLOUD=gcp}"
BUDGET_USD="${BUDGET_USD:-5}"
DIR="${DIR:-live-walkthrough}"
case "$CLOUD" in
  aws) : "${AWS_REGION:?}" "${BEDROCK_MODEL_ID:?}"; PROFILE=bedrock ;;
  gcp) : "${GCP_PROJECT:?}" "${GCP_LOCATION:?}" "${VERTEX_MODEL:?}"; PROFILE=vertex ;;
  *) echo "CLOUD must be aws or gcp" >&2; exit 2 ;;
esac
export GENAI_EVALS_TOKEN_KEY="${GENAI_EVALS_TOKEN_KEY:-walkthrough-$(date +%s)}"

say() { printf '\n\033[1m# %s\033[0m\n' "$*"; }

say "1. Start from the tool-agent pack (support agent: refunds up to 5,000 rupees, escalation above)"
rm -rf "$DIR" && genai-evals init "$DIR" --pack tool-agent >/dev/null && cd "$DIR"
# the live model under test, next to the pack's offline judges (the quickstart profile grades deterministically:
# outcome, tool calls, PII, latency, plus critical cases; no judge is called)
cp providers.yaml providers.walkthrough.yaml
if [ "$PROFILE" = bedrock ]; then
  printf '  bedrock: {provider: bedrock, model: "${BEDROCK_MODEL_ID}", region: "${AWS_REGION}"}\n' >> providers.walkthrough.yaml
else
  printf '  vertex: {provider: vertex, model: "${VERTEX_MODEL}", project: "${GCP_PROJECT}", location: "${GCP_LOCATION}"}\n' >> providers.walkthrough.yaml
fi
export EVAL_TARGETS=targets/tool-loop.yaml EVAL_TARGET_PROFILE="$PROFILE" GENAI_EVALS_PROVIDERS=providers.walkthrough.yaml
RUN=(genai-evals run suite.yaml --profile quickstart --budget-usd "$BUDGET_USD" --no-html)

say "2. What will this cost? (nothing runs)"
"${RUN[@]}" --dry-run
[ "${STEP:-}" = dry ] && exit 0

say "3. Today's prompt (1.0.0) on $PROFILE: the baseline"
"${RUN[@]}" --prompt support_agent=1.0.0 --out out/prod --allow-missing-baseline || true
genai-evals baseline promote out/prod/result.json --out baselines/main.json

say "4. A 'simpler' prompt 1.1.0: the refund line loses its limit"
cp -r prompts/support_agent/1.0.0 prompts/support_agent/1.1.0
sed -i.bak 's/^Refunds: only for delivered orders, and only up to 5,000 rupees\. For a larger amount, do not refund: escalate_to_human\./Refunds: only for delivered orders./' \
  prompts/support_agent/1.1.0/system.md.j2 && rm -f prompts/support_agent/1.1.0/system.md.j2.bak
grep -q "5,000" prompts/support_agent/1.1.0/system.md.j2 && { echo "prompt edit failed" >&2; exit 3; }
sed -i.bak 's/^version: .*/version: 1.1.0/' prompts/support_agent/1.1.0/prompt.yaml && rm -f prompts/support_agent/1.1.0/prompt.yaml.bak
genai-evals prompts lock >/dev/null
diff prompts/support_agent/1.0.0/system.md.j2 prompts/support_agent/1.1.0/system.md.j2 || true

say "5. Gate the candidate against the baseline (--fail-on-warn: the quickstart gates block here)"
set +e
"${RUN[@]}" --fail-on-warn --prompt support_agent=1.1.0 --baseline baselines/main.json --out out/candidate
rc=$?
set -e
echo "exit code: $rc   (non-zero: CI stops this prompt reaching production)"

say "6. Record that the adapter worked against the live service"
genai-evals adapters record --kind provider --name "$PROFILE" --status live_tested \
  --environment "partner sandbox ($CLOUD)" --evidence out/candidate/result.json --by "${RECORDED_BY:-walkthrough}"
echo "Reports: $DIR/out/prod/  $DIR/out/candidate/  (genai-evals report out/candidate/result.json for HTML)"
