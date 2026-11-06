"""Markdown (PR comment / terminal), JUnit XML and JSON outputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def _f(v: Any, pct: bool = True) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.1%}" if pct and -1.5 <= v <= 1.5 else f"{v:,.4g}"
    return str(v)


def markdown_summary(result: dict[str, Any], top_n: int = 5) -> str:
    icon = "✅" if result["passed"] else "❌"
    lines = [f"## {icon} genai-evals: `{result['suite']}`", ""]
    lines.append(f"Run `{result['run_id']}` · {len(result['cases'])} cases × {len(result['targets'])} targets × {result['repeats']} repeats"
                 + (f" · baseline `{result['baseline']}`" if result.get("baseline") else ""))
    pv = result.get("prompt_versions") or {}
    if pv:
        lines.append("Prompt versions: " + "; ".join(f"**{t}**: " + ", ".join(f"`{k}@{v}`" for k, v in d.items()) for t, d in pv.items()))
    lines += ["", "### Gates", "", "| Gate | Target | Result | Values |", "|---|---|---|---|"]
    for g in result["gates"]:
        st = {"pass": "✅ pass", "fail": "❌ fail" if g["severity"] == "block" else "⚠️ fail (warn)", "invalid": "❌ invalid"}[g["status"]]
        vals = ", ".join(f"`{k}`={_f(v)}" for k, v in g["values"].items())
        lines.append(f"| {g['gate']} | {g['target']} | {st} | {vals}{(' — ' + g['message']) if g['message'] else ''} |")
    lines += ["", "### Scores (mean, 95% CI)", ""]
    graders = [g["name"] for g in result["graders"]]
    lines.append("| Target | " + " | ".join(graders) + " | p95 latency | cost / case |")
    lines.append("|---" * (len(graders) + 3) + "|")
    for t, s in result["summary"].items():
        cells = []
        for g in graders:
            d = s["graders"].get(g)
            cells.append("–" if not d or d["mean"] is None else f"{d['mean']:.2f} [{d['ci_low']:.2f}, {d['ci_high']:.2f}]")
        lat = s["ops"]["latency_ms"]["p95"]
        cost = s["ops"]["cost_usd"]["mean"]
        lines.append(f"| {t} | " + " | ".join(cells) + f" | {('%.0f ms' % lat) if lat is not None else '–'} | {('$%.4f' % cost) if cost is not None else '–'} |")
    comps = result.get("comparisons") or {}
    if comps:
        lines += ["", "### Change vs baseline (paired)", "", "| Target | Grader | Δ | 95% CI | Significant | Regressed cases |", "|---|---|---|---|---|---|"]
        for t, gs in comps.items():
            for g, d in gs.items():
                if d.get("n"):
                    reg = ", ".join(f"`{c}`" for c in d.get("regressed", [])[:top_n])
                    lines.append(f"| {t} | {g} | {d['delta']:+.3f} | [{d['ci_low']:+.3f}, {d['ci_high']:+.3f}] | {_f(d['significant'])} | {reg} |")
    fails = _top_failures(result, top_n)
    if fails:
        lines += ["", f"### Top failing cases (up to {top_n})", ""]
        for f in fails:
            lines.append(f"- **{f['target']}** `{f['case_id']}` · {f['grader']}: {f['why']}")
    if result.get("untrusted_graders"):
        lines += ["", "⚠️ Judges below the calibration threshold: " + ", ".join(f"{g} ({w})" for g, w in result["untrusted_graders"].items())]
    return "\n".join(lines) + "\n"


def _top_failures(result: dict[str, Any], n: int) -> list[dict[str, str]]:
    crit = {c["id"] for c in result["cases"] if c.get("critical")}
    fails = [s for s in result["scores"] if s.get("passed") is False]
    fails.sort(key=lambda s: (s["case_id"] not in crit, s.get("value") or 0))
    out, seen = [], set()
    for s in fails:
        k = (s["target"], s["case_id"])
        if k in seen:
            continue
        seen.add(k)
        why = s.get("error") or s.get("reasoning") or s.get("skip_reason") or json.dumps(s.get("details", {}))[:160]
        out.append({"target": s["target"], "case_id": s["case_id"], "grader": s["grader"], "why": str(why)[:200]})
        if len(out) >= n:
            break
    return out


def junit_xml(result: dict[str, Any]) -> str:
    cases_xml = []
    failures = 0
    for g in result["gates"]:
        name = escape(f"gate:{g['gate']}[{g['target']}]")
        if g["passed"] or g["severity"] == "warn":
            cases_xml.append(f'<testcase classname="gates" name="{name}"/>')
        else:
            failures += 1
            msg = escape(f"{g['rule']} -> {g['status']} {g['values']} {g['message']}")
            cases_xml.append(f'<testcase classname="gates" name="{name}"><failure message="{msg}"/></testcase>')
    by_key: dict[tuple[str, str], list[dict]] = {}
    for s in result["scores"]:
        by_key.setdefault((s["target"], s["case_id"]), []).append(s)
    for (t, cid), lst in sorted(by_key.items()):
        bad = [s for s in lst if s.get("passed") is False]
        name = escape(f"{t}:{cid}")
        if bad:
            failures += 1
            msg = escape("; ".join(f"{s['grader']}={s.get('value')} {s.get('error') or s.get('reasoning') or ''}"[:300] for s in bad))
            cases_xml.append(f'<testcase classname="cases.{escape(t)}" name="{name}"><failure message="{msg}"/></testcase>')
        else:
            cases_xml.append(f'<testcase classname="cases.{escape(t)}" name="{name}"/>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="{escape(result["suite"])}" tests="{len(cases_xml)}" failures="{failures}">\n'
            + "\n".join(cases_xml) + "\n</testsuite>\n")


def write_outputs(result: dict[str, Any], out_dir: str | Path, html: bool = True, junit: bool = True) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    (out / "result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    paths["json"] = str(out / "result.json")
    (out / "summary.md").write_text(markdown_summary(result), encoding="utf-8")
    paths["markdown"] = str(out / "summary.md")
    if junit:
        (out / "junit.xml").write_text(junit_xml(result), encoding="utf-8")
        paths["junit"] = str(out / "junit.xml")
    if html:
        from .html import render_report, render_scorecard
        (out / "report.html").write_text(render_report(result), encoding="utf-8")
        (out / "scorecard.html").write_text(render_scorecard(result), encoding="utf-8")
        paths["html"] = str(out / "report.html")
        paths["scorecard"] = str(out / "scorecard.html")
    return paths
