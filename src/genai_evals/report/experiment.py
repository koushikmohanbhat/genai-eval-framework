"""Experiment comparison: Markdown (PR comment / terminal) and a self-contained HTML page. All content is escaped."""
from __future__ import annotations

import json
from html import escape
from typing import Any

from .html import CSS, frontier_svg

VERDICT_CLASS = {"regresses": "bad", "trade-off": "warn", "improves": "ok", "no detectable difference": "skip"}


def _n(v: Any, d: int = 2) -> str:
    return "–" if v is None else f"{v:.{d}f}"


def _sd(v: Any, d: int = 2) -> str:
    return "–" if v is None else f"{v:+.{d}f}"


def _pct(v: Any) -> str:
    return "–" if v is None else f"{v:+.0%}"


def _money(v: Any) -> str:
    return "–" if v is None else f"${v:.4f}"


def _ms(v: Any) -> str:
    return "–" if v is None else f"{v:,.0f} ms"


def _changes_text(ch: dict[str, Any]) -> str:
    if not ch:
        return "no change"
    parts = []
    for k, v in ch.items():
        if k == "options" and isinstance(v, dict):
            parts += [f"options.{ok}={json.dumps(ov, default=str)}" for ok, ov in v.items()]
        else:
            parts.append(f"{k}={v if isinstance(v, str) else json.dumps(v, default=str)}")
    return ", ".join(parts)


def headline(r: dict[str, Any]) -> str:
    bits = []
    if r["critical"]["new_failures"]:
        bits.append(f"{len(r['critical']['new_failures'])} critical case(s) newly fail")
    if r["critical"]["fixed"]:
        bits.append(f"{len(r['critical']['fixed'])} critical case(s) fixed")
    if r["regressions"]:
        bits.append("worse on " + ", ".join(r["regressions"]))
    if r["improvements"]:
        bits.append("better on " + ", ".join(r["improvements"]))
    c = r["ops"]["cost_usd"]["relative"]
    lat = r["ops"]["latency_ms"]["relative"]
    if c is not None and abs(c) >= 0.005:
        bits.append(f"cost {c:+.0%}")
    if lat is not None and abs(lat) >= 0.005:
        bits.append(f"latency {lat:+.0%}")
    return "; ".join(bits) or "no detectable change in quality, cost or latency"


def _ci_label(comp: dict[str, Any]) -> str:
    return f"{1 - comp['alpha_per_test']:.1%} CI"


def _notes(comp: dict[str, Any]) -> list[str]:
    notes = [f"{comp['n_cases']} cases × {comp.get('repeats') or 1} repeat(s), all variants on the same cases, seed and judges."]
    if comp["correction"] == "bonferroni":
        notes.append(f"Significance uses α = {comp['alpha']} split across {comp['tests']} tests (Bonferroni: "
                     f"{comp['alpha_per_test']:.4f} each), so with many variants and graders a change must be larger to count. "
                     f"Quality and cost / latency intervals are shown at that level ({_ci_label(comp)}).")
    else:
        notes.append(f"Significance uses α = {comp['alpha']} per test with no correction for {comp['tests']} tests.")
    notes.append("Slice changes are exploratory: they are not corrected for multiple comparisons. Slices with fewer than "
                 "min_n cases are listed separately and not ranked.")
    if comp.get("dropped_gates"):
        notes.append("Gates that compare with a baseline run were not applied (the comparison replaces them): "
                     + ", ".join(comp["dropped_gates"]) + ".")
    return notes


# ---- Markdown ---------------------------------------------------------------
MONTHLY_CAVEAT = ("Eval cost per case is not production cost per request: eval prompts, repeats and lengths differ from "
                  "live traffic, and the judge's cost is not included. Treat this as the direction and rough size of the "
                  "change; for a business case, put measured production token counts into the ROI calculator "
                  "(genai-roi-calculator).")


def monthly(comp: dict[str, Any], volume: int) -> list[dict[str, Any]]:
    """Per variant: model cost per month at `volume` requests, baseline vs variant, and the change with the paired
    interval scaled to the same volume, next to the quality verdict. None where the run carried no prices."""
    out = []
    for v in comp["variants"]:
        r = comp["results"][v["name"]]
        c = r["ops"]["cost_usd"]
        b, n = c.get("baseline_mean"), c.get("candidate_mean")
        row = {"variant": v["name"], "verdict": r["verdict"], "volume": volume,
               "baseline_usd": b * volume if b is not None else None,
               "variant_usd": n * volume if n is not None else None,
               "change_usd": (n - b) * volume if b is not None and n is not None else None,
               "change_low_usd": c["ci_low"] * volume if c.get("ci_low") is not None else None,
               "change_high_usd": c["ci_high"] * volume if c.get("ci_high") is not None else None,
               "significant": c.get("significant", False),
               "quality": {g: {"delta": q["delta"], "ci_low": q["ci_low"], "ci_high": q["ci_high"],
                               "significant": q["significant"]} for g, q in r["quality"].items()}}
        out.append(row)
    return out


def _monthly_md(comp: dict[str, Any]) -> list[str]:
    rows = comp.get("monthly")
    if not rows:
        return []
    L = ["", f"### Monthly model cost at {rows[0]['volume']:,} requests / month", "",
         "| Variant | Baseline / month | Variant / month | Change | Interval | Quality |", "|---|---|---|---|---|---|"]
    for m in rows:
        rng = (f"[{_money(m['change_low_usd'])}, {_money(m['change_high_usd'])}]" if m["change_low_usd"] is not None else "")
        q = "; ".join(f"{g} {_sd(x['delta'])}{' sig' if x['significant'] else ''}" for g, x in m["quality"].items())
        L.append(f"| {m['variant']} | {_money(m['baseline_usd'])} | {_money(m['variant_usd'])} | {_money(m['change_usd'])} "
                 f"| {rng} | {m['verdict']}: {q} |")
    return L + ["", f"_{MONTHLY_CAVEAT}_"]


def _monthly_html(comp: dict[str, Any]) -> str:
    rows = comp.get("monthly")
    if not rows:
        return ""
    body = "".join(
        f"<tr><td><b>{escape(m['variant'])}</b></td><td class='num'>{_money(m['baseline_usd'])}</td>"
        f"<td class='num'>{_money(m['variant_usd'])}</td><td class='num'>{_money(m['change_usd'])}</td>"
        f"<td class='num'>{'[' + _money(m['change_low_usd']) + ', ' + _money(m['change_high_usd']) + ']' if m['change_low_usd'] is not None else ''}</td>"
        f"<td>{_pill(m['verdict'])}</td></tr>" for m in rows)
    return (f"<h2>Monthly model cost at {rows[0]['volume']:,} requests / month</h2><table><tr><th>Variant</th>"
            f"<th>Baseline / month</th><th>Variant / month</th><th>Change</th><th>Interval</th><th>Quality verdict</th></tr>"
            f"{body}</table><p class='mut'>{escape(MONTHLY_CAVEAT)}</p>")


def render_comparison_md(comp: dict[str, Any], max_cases: int = 10) -> str:
    L = [f"## Experiment: {comp['name']}", ""]
    if comp.get("description"):
        L += [comp["description"], ""]
    if comp.get("hypothesis"):
        L += [f"**Hypothesis:** {comp['hypothesis']}", ""]
    L += [f"Baseline: **{comp['baseline']}**", "",
          "| Variant | Change | Verdict | Summary |", "|---|---|---|---|"]
    for v in comp["variants"]:
        r = comp["results"][v["name"]]
        L.append(f"| **{v['name']}** | {_changes_text(v['changes'])} | {r['verdict']} | {headline(r)} |")
    L += _monthly_md(comp)
    for v in comp["variants"]:
        r = comp["results"][v["name"]]
        L += ["", f"### {v['name']} vs {comp['baseline']}: {r['verdict']}", ""]
        if v.get("description"):
            L += [v["description"], ""]
        L += [f"| Grader | Baseline | Variant | Δ | {_ci_label(comp)} | pass^k | New failures | Fixed |", "|---|---|---|---|---|---|---|---|"]
        for g, q in r["quality"].items():
            mark = " **sig**" if q["significant"] else ""
            L.append(f"| {g} | {_n(q['baseline'])} | {_n(q['candidate'])} | {_sd(q['delta'])}{mark} | [{_sd(q['ci_low'])}, {_sd(q['ci_high'])}] "
                     f"| {_n(q['baseline_pass_hat_k'])} → {_n(q['candidate_pass_hat_k'])} | {len(q['new_failures'])} | {len(q['fixed'])} |")
        o = r["ops"]
        L += ["", "| Cost / latency | Baseline | Variant | Change |", "|---|---|---|---|",
              f"| cost per case | {_money(o['cost_usd']['baseline_mean'])} | {_money(o['cost_usd']['candidate_mean'])} | {_pct(o['cost_usd']['relative'])} |",
              f"| latency (mean) | {_ms(o['latency_ms']['baseline_mean'])} | {_ms(o['latency_ms']['candidate_mean'])} | {_pct(o['latency_ms']['relative'])} |",
              f"| latency (p95) | {_ms(o['latency_ms']['baseline_p95'])} | {_ms(o['latency_ms']['candidate_p95'])} | |",
              f"| errors | {o['errors']['baseline']} | {o['errors']['candidate']} | |"]
        c = r["critical"]
        L += ["", f"**Critical cases:** {c['total'] - len(c['baseline_failed'])}/{c['total']} → {c['total'] - len(c['candidate_failed'])}/{c['total']} pass"
              + (f"; newly failing: {', '.join(c['new_failures'])}" if c["new_failures"] else "")
              + (f"; fixed: {', '.join(c['fixed'])}" if c["fixed"] else "")]
        if r["slices"]:
            L += ["", "**Slices that moved most**", "", "| Slice | Grader | n | Baseline | Variant | Δ |", "|---|---|---|---|---|---|"]
            L += [f"| {s['slice']} | {s['grader']} | {s['n']} | {_n(s['baseline'])} | {_n(s['candidate'])} | {_sd(s['delta'])} |" for s in r["slices"]]
        if r["slices_below_min_n"]:
            L += ["", "Below min_n (not ranked): " + ", ".join(f"{s['slice']}/{s['grader']} {_sd(s['delta'])} (n={s['n']})"
                                                           for s in r["slices_below_min_n"])]
        if r["failure_types"]:
            L += ["", "**Failure types**: " + "; ".join(f"{f['grader']} +{f['new_failures']} new / −{f['fixed']} fixed" for f in r["failure_types"])]
        if r["flipped_cases"]:
            L += ["", f"**Cases that flipped** ({len(r['flipped_cases'])}):"]
            for fc in r["flipped_cases"][:max_cases]:
                ch = ", ".join(f"{x['grader']} {x['change']}" for x in fc["changes"])
                L.append(f"- `{fc['case_id']}`{' (critical)' if fc['critical'] else ''}: {ch}")
            if len(r["flipped_cases"]) > max_cases:
                L.append(f"- … and {len(r['flipped_cases']) - max_cases} more (see comparison.json)")
    L += [""] + [f"_{n}_" for n in _notes(comp)]
    return "\n".join(L) + "\n"


# ---- HTML -------------------------------------------------------------------
def _pill(verdict: str) -> str:
    return f'<span class="pill {VERDICT_CLASS.get(verdict, "skip")}">{escape(verdict)}</span>'


def _quality_table(r: dict[str, Any], ci_label: str = "CI") -> str:
    rows = []
    for g, q in r["quality"].items():
        cls = "ok" if q["significant"] and q["delta"] > 0 else ("bad" if q["significant"] and q["delta"] < 0 else "")
        sig = _pill("improves" if cls == "ok" else "regresses") if cls else '<span class="mut">n.s.</span>'
        rows.append(f"<tr><td>{escape(g)}</td><td class='num'>{_n(q['baseline'])}</td><td class='num'>{_n(q['candidate'])}</td>"
                    f"<td class='num'>{_sd(q['delta'])}</td><td class='num'>[{_sd(q['ci_low'])}, {_sd(q['ci_high'])}]</td><td>{sig}</td>"
                    f"<td class='num'>{_n(q['baseline_pass_hat_k'])} → {_n(q['candidate_pass_hat_k'])}</td>"
                    f"<td class='num'>{len(q['new_failures'])}</td><td class='num'>{len(q['fixed'])}</td></tr>")
    return (f"<table><tr><th>Grader</th><th>Baseline</th><th>Variant</th><th>Δ</th><th>{escape(ci_label)}</th><th>Significant</th>"
            "<th>pass^k</th><th>New failures</th><th>Fixed</th></tr>" + "".join(rows) + "</table>")


def _ops_table(r: dict[str, Any]) -> str:
    o = r["ops"]
    c, lat = o["cost_usd"], o["latency_ms"]
    return ("<table><tr><th>Cost / latency</th><th>Baseline</th><th>Variant</th><th>Change</th></tr>"
            f"<tr><td>cost per case</td><td class='num'>{_money(c['baseline_mean'])}</td><td class='num'>{_money(c['candidate_mean'])}</td>"
            f"<td class='num'>{_pct(c['relative'])}</td></tr>"
            f"<tr><td>latency (mean)</td><td class='num'>{_ms(lat['baseline_mean'])}</td><td class='num'>{_ms(lat['candidate_mean'])}</td>"
            f"<td class='num'>{_pct(lat['relative'])}</td></tr>"
            f"<tr><td>latency (p95)</td><td class='num'>{_ms(o['latency_ms']['baseline_p95'])}</td><td class='num'>{_ms(o['latency_ms']['candidate_p95'])}</td><td></td></tr>"
            f"<tr><td>errors</td><td class='num'>{o['errors']['baseline']}</td><td class='num'>{o['errors']['candidate']}</td><td></td></tr></table>")


def _slices_table(r: dict[str, Any]) -> str:
    if not r["slices"] and not r["slices_below_min_n"]:
        return "<p class='mut'>No slice changed.</p>"
    note = ("" if r["slices"] else f"<p class='mut'>No changed slice has at least {r['min_n']} cases, so none is ranked; "
            "treat these as leads to investigate, not findings.</p>")
    rows = [f"<tr><td>{escape(s['slice'])}</td><td>{escape(s['grader'])}</td><td class='num'>{s['n']}</td><td class='num'>{_n(s['baseline'])}</td>"
            f"<td class='num'>{_n(s['candidate'])}</td><td class='num'>{_sd(s['delta'])}</td><td class='num'>[{_sd(s.get('ci_low'))}, {_sd(s.get('ci_high'))}]</td></tr>"
            for s in r["slices"]]
    small = "".join(f"<tr class='mut'><td>{escape(s['slice'])}</td><td>{escape(s['grader'])}</td><td class='num'>{s['n']}</td><td class='num'>{_n(s['baseline'])}</td>"
                    f"<td class='num'>{_n(s['candidate'])}</td><td class='num'>{_sd(s['delta'])}</td><td>below min_n</td></tr>" for s in r["slices_below_min_n"])
    return (note + "<table><tr><th>Slice</th><th>Grader</th><th>n</th><th>Baseline</th><th>Variant</th><th>Δ</th><th>CI</th></tr>"
            + "".join(rows) + small + "</table>")


def _flips(r: dict[str, Any]) -> str:
    ft = r["failure_types"]
    head = ("<table><tr><th>Failure type (grader)</th><th>New failures</th><th>Fixed</th></tr>"
            + "".join(f"<tr><td>{escape(f['grader'])}</td><td class='num'>{f['new_failures']}</td><td class='num'>{f['fixed']}</td></tr>" for f in ft)
            + "</table>") if ft else "<p class='mut'>No case changed pass / fail on any grader.</p>"
    items = []
    for fc in r["flipped_cases"]:
        ch = " ".join(f"<span class='pill {'bad' if x['change'] == 'new failure' else 'ok'}'>{escape(x['grader'])}: {escape(x['change'])}</span>"
                      for x in fc["changes"])
        tags = "".join(f"<span class='tag'>{escape(t)}</span>" for t in fc["tags"])
        crit = ' <span class="pill bad">critical</span>' if fc["critical"] else ""
        items.append(f"<tr><td><code>{escape(fc['case_id'])}</code>{crit}</td><td>{tags}</td><td>{ch}</td></tr>")
    cases = ("<details><summary>Cases that flipped (" + str(len(items)) + ")</summary><div class='body'><table><tr><th>Case</th><th>Tags</th><th>Change</th></tr>"
             + "".join(items) + "</table></div></details>") if items else ""
    return head + cases


def _gates(comp: dict[str, Any], name: str) -> str:
    gs = comp.get("gates", {}).get(name, [])
    if not gs:
        return ""
    pills = []
    for g in gs:
        status = g["status"] or ("pass" if g["passed"] else "fail")
        cls = "ok" if g["passed"] else ("warn" if g["severity"] == "warn" else "bad")
        pills.append(f"<span class='pill {cls}'>{escape(g['gate'])}: {escape(status)}</span>")
    return "<p>Gates: " + " ".join(pills) + "</p>"


def render_comparison_html(comp: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    rows = "".join(f"<tr><td><b>{escape(v['name'])}</b><div class='mut'>{escape(v.get('description') or '')}</div></td>"
                   f"<td>{escape(_changes_text(v['changes']))}</td><td>{_pill(comp['results'][v['name']]['verdict'])}</td>"
                   f"<td>{escape(headline(comp['results'][v['name']]))}</td></tr>" for v in comp["variants"])
    sections = []
    for v in comp["variants"]:
        r = comp["results"][v["name"]]
        c = r["critical"]
        sections.append(
            f"<h2 id='{escape(v['name'])}'>{escape(v['name'])} vs {escape(comp['baseline'])} {_pill(r['verdict'])}</h2>"
            f"<p class='mut'>{escape(_changes_text(v['changes']))}</p>{_gates(comp, v['name'])}"
            f"<h3>Quality</h3>{_quality_table(r, _ci_label(comp))}<h3>Cost and latency</h3>{_ops_table(r)}"
            f"<h3>Critical cases</h3><p>{c['total'] - len(c['baseline_failed'])}/{c['total']} → {c['total'] - len(c['candidate_failed'])}/{c['total']} pass"
            + (f" · newly failing: {escape(', '.join(c['new_failures']))}" if c["new_failures"] else "")
            + (f" · fixed: {escape(', '.join(c['fixed']))}" if c["fixed"] else "") + "</p>"
            f"<h3>Slices that moved most</h3>{_slices_table(r)}<h3>Failure types and cases</h3>{_flips(r)}")
    charts = ""
    if result:
        g = next(iter(next(iter(comp["results"].values()))["quality"]), None) if comp["results"] else None
        charts = (f"<h2>Quality vs cost and latency</h2><div class='grid'><div>{frontier_svg(result, g, 'cost')}</div>"
                  f"<div>{frontier_svg(result, g, 'latency')}</div></div>")
    hyp = f"<p><b>Hypothesis:</b> {escape(comp['hypothesis'])}</p>" if comp.get("hypothesis") else ""
    run_link = "<p><a href='run/report.html'>Full run report</a> · <a href='run/scorecard.html'>scorecard</a> · <a href='manifest.json'>manifest</a></p>" if result else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Experiment · {escape(comp['name'])}</title><style>{CSS}h3{{font-size:14px;margin:18px 0 6px}}code{{font-size:12.5px}}</style></head>
<body><main><h1>Experiment · {escape(comp['name'])}</h1><div class="mut">{escape(comp.get('description') or '')}</div>{hyp}
<p>Baseline: <b>{escape(comp['baseline'])}</b> · {comp['n_cases']} cases × {comp.get('repeats') or 1} repeat(s)</p>{run_link}
<table><tr><th>Variant</th><th>Change</th><th>Verdict</th><th>Summary</th></tr>{rows}</table>
{_monthly_html(comp)}{charts}{''.join(sections)}
<div style="margin-top:30px">{''.join(f"<p class='mut'>{escape(n)}</p>" for n in _notes(comp))}</div>
</main></body></html>"""
