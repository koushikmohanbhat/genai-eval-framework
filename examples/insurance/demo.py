"""Insurance demo-in-a-box: fully offline, no API keys, about 10 seconds.

    python examples/insurance/demo.py            # from anywhere; outputs go to ./genai-evals-demo/

What it shows (each step is one CLI command you can run yourself):
  1. Claim-form extraction: two systems on 24 cases; field accuracy with CIs, a Hinglish slice and critical high-value claims.
  2. Policy Q&A (RAG): retrieval metrics vs answer groundedness, citations, correct refusals, a two-judge panel,
     and judge calibration against human labels.
  3. Claims agent: prompt 1.3.2 (prod) becomes the baseline; candidate 1.4.0 fixes injury escalation but stops
     verifying policies. Average task success is unchanged, yet the critical-case gate blocks the release.
  4. DPDP: prompt 1.4.0 adds a data element (HEALTH_INFO) and promotion to prod is blocked until acknowledged.
  5. Online evals over recorded production telemetry from AWS (AgentCore spans in CloudWatch + Bedrock invocation
     logs + AgentCore Memory + AgentCore Evaluations) and Google Cloud (Cloud Trace + Vertex request-response logs in
     BigQuery + Agent Engine Sessions), graded side by side, published as CloudWatch EMF metrics.
  6. Promptfoo export of the live suite.
Steps 1-3 and 5 include gates that are *expected* to fail; the demo checks that they fail for the right reasons.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
os.environ.setdefault("GENAI_EVALS_TOKEN_KEY", "demo-key-not-secret")

from genai_evals.cli import main as cli  # noqa: E402

OUT = Path.cwd() / "genai-evals-demo"


def step(title: str) -> None:
    print("\n" + "=" * 100 + f"\n{title}\n" + "=" * 100)


def run(*argv: str) -> int:
    print("$ genai-evals " + " ".join(argv))
    return cli(list(argv))


def load(p: Path) -> dict:
    return json.loads((p / "result.json").read_text())


def gate(res: dict, name: str, target: str) -> dict:
    return next(g for g in res["gates"] if g["gate"] == name and g["target"] == target)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    os.chdir(HERE)
    checks: list[tuple[str, bool]] = []

    step("1. Claim-form extraction: extractor-v1 vs extractor-v2 (simulated systems, costs/latency simulated)")
    run("run", "claims_extraction.yaml", "--out", str(OUT / "1-extraction"), "--no-cache", "--store", f"sqlite:///{OUT}/results.db")
    r = load(OUT / "1-extraction")
    checks.append(("v2 passes field accuracy", gate(r, "field-accuracy", "extractor-v2")["passed"]))
    checks.append(("v1 fails the Hinglish slice", not gate(r, "hinglish-slice", "extractor-v1")["passed"]))

    step("2. Policy Q&A (RAG) with a two-judge panel, then judge calibration against human labels")
    (HERE / "calibration.json").unlink(missing_ok=True)          # start uncalibrated, so the demo is reproducible
    run("run", "policy_qa.yaml", "--out", str(OUT / "2-rag-uncalibrated"), "--no-cache")
    r0 = load(OUT / "2-rag-uncalibrated")
    checks.append(("rag-v1 adds unsupported claims (grounded gate fails)", not gate(r0, "grounded", "rag-v1")["passed"]))
    checks.append(("rag-v2 refuses out-of-scope questions", gate(r0, "refuses-out-of-scope", "rag-v2")["passed"]))
    run("calibrate", str(OUT / "2-rag-uncalibrated" / "result.json"), "--grader", "faithfulness", "--labels",
        "data/faithfulness_labels.csv", "--out", "calibration.json", "--min-kappa", "0.4")
    run("run", "policy_qa.yaml", "--out", str(OUT / "2-rag"), "--no-cache")   # same suite, now with calibration.json
    r = load(OUT / "2-rag")
    checks.append(("faithfulness judge untrusted on 40 labels: grounded gate invalid",
                   gate(r, "grounded", "rag-v2").get("status") == "invalid"))

    step("3. Claims agent: prod prompt 1.3.2 -> baseline; candidate 1.4.0 gated against it (3 repeats each)")
    # first run: there is no baseline yet, so the no-regression gate is explicitly allowed to pass
    run("run", "fnol_agent.yaml", "--out", str(OUT / "3-agent-prod"), "--no-cache", "--allow-missing-baseline")
    run("baseline", "promote", str(OUT / "3-agent-prod" / "result.json"), "--name", "prod", "--out", str(OUT / "baseline-prod.json"))
    rc = run("run", "fnol_agent.yaml", "--prompt", "claims_agent=1.4.0", "--baseline", str(OUT / "baseline-prod.json"),
             "--out", str(OUT / "3-agent-candidate"), "--no-cache", "--summary-md", str(OUT / "3-agent-candidate" / "pr-comment.md"))
    base, cand = load(OUT / "3-agent-prod"), load(OUT / "3-agent-candidate")
    comp = cand["comparisons"]["fnol-agent"]
    checks.append(("prod 1.3.2 passes all blocking gates", base["passed"]))
    checks.append(("candidate 1.4.0 blocked by critical cases", rc == 1 and not gate(cand, "critical-cases", "fnol-agent")["passed"]))
    checks.append(("injury escalation improved", comp["escalated_injury"]["delta"] > 0))
    print(f"\n  task_success Δ {comp['task_success']['delta']:+.3f} (not significant) — the average hides it; "
          f"critical cases failing: {cand['summary']['fnol-agent']['critical']['failed']}")

    step("3b. Experiment: prod prompt vs prompt 1.4.0 vs a faster model, same cases, compared side by side and reproduced")
    run("experiment", "run", "fnol_experiment.yaml", "--out", str(OUT / "3-experiment"), "--no-cache")
    exp = json.loads((OUT / "3-experiment" / "comparison.json").read_text())
    checks.append(("experiment: 1.4.0 regresses (4 new critical failures) despite better injury escalation",
                   exp["results"]["prompt-1.4.0"]["verdict"] == "regresses"
                   and len(exp["results"]["prompt-1.4.0"]["critical"]["new_failures"]) == 4
                   and "escalated_injury" in exp["results"]["prompt-1.4.0"]["improvements"]))
    fast = exp["results"]["fast-model"]
    checks.append(("experiment: fast model cuts cost by more than half but skips policy checks",
                   fast["ops"]["cost_usd"]["relative"] < -0.5 and "verified_policy" in fast["regressions"]))
    run("experiment", "rerun", str(OUT / "3-experiment"), "--out", str(OUT / "3-experiment-rerun"), "--no-cache")
    rows = json.loads((OUT / "3-experiment-rerun" / "reproduction.json").read_text())
    checks.append(("experiment reproduces from its manifest", bool(rows) and all(r["status"] == "reproduced" for r in rows)))

    step("4. DPDP: prompt 1.4.0 touches a new data element; promotion to prod is blocked until acknowledged")
    run("prompts", "check")
    rc = run("prompts", "promote", "claims_agent", "1.4.0", "--stage", "prod", "--lock", str(HERE / "prompts.lock"))
    checks.append(("promotion blocked for new data element", rc == 2))

    step("5. Online evals: recorded production telemetry from AWS and Google Cloud, graded side by side")
    run("online", "--sources", "sources.yaml", "--suite", "online_checks.yaml", "--out", str(OUT / "5-online"), "--publish", "emf")
    r = load(OUT / "5-online")
    checks.append(("AWS: phone echo and payout promise caught", not gate(r, "privacy", "claims-agent-aws")["passed"]
                   and not gate(r, "no-payout-promises", "claims-agent-aws")["passed"]))
    checks.append(("GCP prompts recovered from BigQuery logs", all(s["output"] for s in r["samples"] if s["target"] == "claims-agent-gcp")))
    checks.append(("AgentCore Evaluations scores imported", any(g.startswith("agentcore:") for g in r["summary"]["claims-agent-aws"]["graders"])))

    step("6. Promptfoo export of the live (multi-provider) suite")
    run("promptfoo", "export", "claims_extraction.live.yaml", "--providers", "providers.live.yaml", "-o", str(OUT / "promptfooconfig.yaml"))

    step("Result")
    for name, ok in checks:
        print(f"  [{'ok' if ok else 'XX'}] {name}")
    print(f"\nReports: {OUT}/*/report.html and scorecard.html · PR comment: {OUT}/3-agent-candidate/pr-comment.md")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
