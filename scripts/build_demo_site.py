"""Rebuild the browsable demo (docs/demo/) and the README screenshots (docs/img/) from the offline demo.

    python scripts/build_demo_site.py              # reports + screenshots (screenshots need Playwright + Chromium)
    python scripts/build_demo_site.py --no-shots   # reports only

Everything is generated from examples/insurance/demo.py, so the published pages always match the code.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_OUT = ROOT / "docs" / "demo"
IMG_OUT = ROOT / "docs" / "img"

SCENARIOS = [
    ("1-extraction", "Claim-form extraction: v1 vs v2",
     "Two extractors on 24 claim forms. Field-level accuracy with Indian date, amount and name normalisers. "
     "v1 fails the Hinglish slice and the high-value critical cases; v2 passes every gate."),
    ("2-rag", "Policy Q&A (RAG) with a two-judge panel",
     "Claim-level faithfulness from two judges, retrieval recall, citations and correct refusals. "
     "rag-v1 adds unsupported claims. After calibration against 40 human labels the faithfulness judge is "
     "untrusted (the CI on kappa is too wide), so the grounded gate is reported invalid instead of passing."),
    ("3-agent-prod", "Claims agent: prompt 1.3.2 (prod)",
     "A tool-calling FNOL agent, 16 cases x 3 repeats: task success, pass^k, tool-call checks, policy and privacy. "
     "This run becomes the baseline."),
    ("3-agent-candidate", "Claims agent: prompt 1.4.0 blocked",
     "The candidate prompt keeps average task success unchanged (not significant) but fails four critical "
     "cases, so the release gate blocks it. Includes the PR comment CI would post."),
    ("3-experiment", "Experiment: prompt 1.4.0 and a faster model vs prod",
     "Baseline and two candidate changes on the same 16 cases x 3 repeats. Quality, cost, latency and critical cases "
     "side by side, the slices and failure types that changed, and a manifest that lets the run be reproduced. "
     "1.4.0 fixes injury escalation but newly fails 4 critical cases; the faster model costs 65% less but skips "
     "policy checks."),
    ("5-online", "Online evals: AWS and Google Cloud side by side",
     "Recorded production telemetry: AgentCore spans from CloudWatch joined with Bedrock invocation logs, and Cloud "
     "Trace spans joined with Vertex request-response logs from BigQuery. Catches a phone-number echo and a payout "
     "promise on AWS; AgentCore Evaluations scores are imported alongside. Framed as monitoring: graded vs read "
     "counts per source, reference-free signals, not a release verdict."),
]


PACK_SCENARIOS = [
    ("pack-rag-knowledge-assistant", "Pack: RAG knowledge assistant (offline, as `init` leaves it)",
     "The project `genai-evals init --pack rag-knowledge-assistant` creates, run on its simulator: retrieval and answer "
     "quality, citations, refusals, privacy and prompt-injection safety, with the pack's failure categories."),
    ("pack-tool-agent", "Pack: tool-using agent (offline)",
     "Order support agent on mock tools, 16 conversations x 3 repeats: outcomes, tool use, policy edges, escalation, pass^k."),
    ("pack-structured-extraction", "Pack: structured extraction with the India GST overlay (offline)",
     "Invoices from clean PDFs, emails and OCR text, plus the GST overlay: schema validity, field accuracy, totals and "
     "currencies, rejection of credit notes."),
]
SCENARIOS += PACK_SCENARIOS


def run_demo(work: Path) -> Path:
    subprocess.run([sys.executable, str(ROOT / "examples" / "insurance" / "demo.py")], cwd=work, check=True,
                   stdout=subprocess.DEVNULL)
    out = work / "genai-evals-demo"
    from genai_evals import packs
    for slug, _, _ in PACK_SCENARIOS:
        name = slug.removeprefix("pack-")
        overlays = [o["name"] for o in packs.manifest(packs.resolve(name)).get("overlays", [])][:1] if name == "structured-extraction" else []
        proj = packs.scaffold(name, work / "packs" / name, overlays=overlays)
        packs.run_offline(proj, work / "packs-out" / name, online=False)
        shutil.copytree(work / "packs-out" / name / "offline", out / slug)
    return out


def copy_reports(src: Path) -> None:
    if DEMO_OUT.exists():
        shutil.rmtree(DEMO_OUT)
    DEMO_OUT.mkdir(parents=True)
    for slug, _, _ in SCENARIOS:
        dst = DEMO_OUT / slug
        dst.mkdir()
        for name in ("report.html", "scorecard.html", "summary.md", "pr-comment.md", "comparison.html", "comparison.md",
                     "run/report.html", "run/scorecard.html"):
            if (src / slug / name).exists():
                (dst / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / slug / name, dst / name)
        if (src / slug / "manifest.json").exists():   # drop local paths and machine details from the published copy
            m = json.loads((src / slug / "manifest.json").read_text(encoding="utf-8"))
            for f in m.get("files", {}).values():
                f["path"] = os.path.relpath(f["path"], ROOT)
            m["experiment_path"] = os.path.relpath(m["experiment_path"], ROOT)
            m["environment"].pop("platform", None)
            (dst / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    shutil.copy2(src / "promptfooconfig.yaml", DEMO_OUT / "promptfooconfig.yaml")
    (ROOT / "docs" / ".nojekyll").write_text("")  # serve files as-is (docs contain ${{ }} that Jekyll would parse)
    cards = []
    for slug, title, text in SCENARIOS:
        extra = ' · <a href="%s/pr-comment.md">PR comment</a>' % slug if (DEMO_OUT / slug / "pr-comment.md").exists() else ""
        if (DEMO_OUT / slug / "comparison.html").exists():
            links = (f'<a href="{slug}/comparison.html">Comparison</a> · <a href="{slug}/run/report.html">Full run report</a> · '
                     f'<a href="{slug}/manifest.json">Manifest</a>')
        else:
            links = f'<a href="{slug}/report.html">Engineer report</a> · <a href="{slug}/scorecard.html">Exec scorecard</a>{extra}'
        cards.append(f'<div class="card"><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p><p class="links">{links}</p></div>')
    (DEMO_OUT / "index.html").write_text(INDEX.replace("{{CARDS}}", "\n".join(cards)), encoding="utf-8")


INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>GenAI Eval Framework: sample reports</title>
<style>
body{font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;margin:40px auto;padding:0 20px;color:#1f2328;line-height:1.5}
h1{font-size:26px;margin-bottom:4px} .sub{color:#59636e;margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px;margin-top:24px}
.card{border:1px solid #d1d9e0;border-radius:10px;padding:16px 18px} .card h2{font-size:16px;margin:0 0 6px}
.card p{margin:6px 0;font-size:14px;color:#3d444d} .links a{font-weight:600;color:#0969da;text-decoration:none}
.note{margin-top:28px;font-size:13px;color:#59636e}
</style></head><body>
<h1>GenAI Eval Framework: sample reports</h1>
<p class="sub">Generated by the offline insurance demo (<code>python examples/insurance/demo.py</code>): simulated
systems and recorded telemetry, no API keys. Every page below is the framework's real output.</p>
<div class="grid">
{{CARDS}}
</div>
<p class="note">Engineer report: gates, scores with 95% confidence intervals, slices, and a per-case drill-down with
inputs, outputs, tool calls and judge reasoning. Exec scorecard: one page for a steering committee.
<a href="promptfooconfig.yaml">Promptfoo export</a> of the multi-provider suite.
· <a href="https://github.com/koushikmohanbhat/genai-eval-framework">Repository</a>
· <a href="https://github.com/koushikmohanbhat/genai-eval-framework/blob/main/docs/demo-walkthrough.md">Walkthrough</a></p>
</body></html>
"""


SHOTS = [
    # (file, page, selector or None for viewport, viewport height, actions)
    ("scorecard-compare.png", "1-extraction/scorecard.html", None, 760, None),
    ("report-gates-blocked.png", "3-agent-candidate/report.html", None, 820, None),
    ("report-case-drilldown.png", "2-rag/report.html", "details.case[data-search*='settled within 7 days']", 0, "open-first-failing"),
    ("online-aws-gcp.png", "5-online/scorecard.html", None, 640, None),
    ("experiment-comparison.png", "3-experiment/comparison.html", None, 980, None),
]


def screenshots() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed: skipping screenshots (pip install playwright && playwright install chromium)")
        return
    IMG_OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]) if chrome else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        for name, rel, selector, height, action in SHOTS:
            page.set_viewport_size({"width": 1200, "height": height or 800})
            page.goto((DEMO_OUT / rel).as_uri())
            if action == "open-first-failing":
                el = page.locator(selector).first
                el.evaluate("e => e.open = true")
                el.screenshot(path=str(IMG_OUT / name))
            else:
                page.screenshot(path=str(IMG_OUT / name))
            print(f"  docs/img/{name}")
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-shots", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        src = run_demo(Path(tmp))
        copy_reports(src)
    print(f"reports -> {DEMO_OUT.relative_to(ROOT)}/")
    if not args.no_shots:
        screenshots()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
