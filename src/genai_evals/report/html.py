"""Self-contained HTML: engineer report (per-case drill-down) and a one-page exec scorecard. All content is escaped."""
from __future__ import annotations

import json
from html import escape
from typing import Any

CSS = """
:root{--fg:#1d1d1f;--mut:#6b6b70;--bd:#e3e3e8;--bg:#fafafa;--ok:#1a7f4b;--bad:#b42318;--warn:#a15c00;--acc:#2b59c3}
*{box-sizing:border-box}body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--fg);margin:0;background:#fff}
main{max-width:1180px;margin:0 auto;padding:28px 24px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.mut{color:var(--mut)}table{border-collapse:collapse;width:100%;margin:6px 0 4px}th,td{border-bottom:1px solid var(--bd);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.02em}td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}.ok{background:#e6f4ec;color:var(--ok)}.bad{background:#fdecea;color:var(--bad)}
.warn{background:#fff4e0;color:var(--warn)}.skip{background:#f0f0f3;color:var(--mut)}
.verdict{font-size:15px;font-weight:600;padding:10px 14px;border-radius:8px;margin:14px 0}.verdict.ok{background:#e6f4ec}.verdict.bad{background:#fdecea}
.ci{position:relative;height:8px;background:#eef0f4;border-radius:4px;width:120px;display:inline-block;margin-left:8px;vertical-align:middle}
.ci span{position:absolute;top:0;height:8px;background:#9db3e6;border-radius:4px}.ci i{position:absolute;top:-2px;width:2px;height:12px;background:var(--acc)}
details{border:1px solid var(--bd);border-radius:8px;margin:8px 0;background:#fff}summary{cursor:pointer;padding:8px 12px;list-style:none}
summary::-webkit-details-marker{display:none}details[open] summary{border-bottom:1px solid var(--bd)}.body{padding:10px 14px}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:8px;margin:4px 0 10px;font-size:12.5px;max-height:340px;overflow:auto}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}
select,input{font:inherit;padding:4px 8px;border:1px solid var(--bd);border-radius:6px}.k{font-weight:600}.tag{font-size:11px;color:var(--mut);border:1px solid var(--bd);border-radius:8px;padding:0 6px;margin-right:4px}
.card{border:1px solid var(--bd);border-radius:10px;padding:14px 16px}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.big{font-size:28px;font-weight:700}a{color:var(--acc)}
"""

JS = """
function f(){const t=document.getElementById('ft').value,s=document.getElementById('fs').value,q=document.getElementById('fq').value.toLowerCase();
document.querySelectorAll('details.case').forEach(d=>{const ok=(!t||d.dataset.target===t)&&(!s||(s==='fail'?d.dataset.fail==='1':d.dataset.fail==='0'))&&(!q||d.dataset.search.includes(q));d.style.display=ok?'':'none'})}
"""


def _pct(v: Any) -> str:
    return "–" if v is None else f"{v:.2f}"


def _pill(status: str, text: str | None = None) -> str:
    cls = {"pass": "ok", "fail": "bad", "invalid": "bad", "warn": "warn", "skip": "skip"}.get(status, "skip")
    return f'<span class="pill {cls}">{escape(text or status)}</span>'


def _ci_bar(d: dict) -> str:
    if not d or d.get("mean") is None:
        return ""
    lo, hi, m = max(0, d["ci_low"]), min(1, d["ci_high"]), d["mean"]
    return f'<span class="ci" title="95% CI {lo:.2f}–{hi:.2f}"><span style="left:{lo*100:.1f}%;width:{max(1,(hi-lo)*100):.1f}%"></span><i style="left:{m*100:.1f}%"></i></span>'


def _primary_grader(result: dict) -> str | None:
    import re
    head = (result.get("report") or {}).get("headline")
    if head and any(x["name"] == head for x in result.get("graders", [])):
        return head
    for g in result["gates"]:
        m = re.search(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)", g["rule"])
        if m and any(x["name"] == m.group(1) for x in result["graders"]):
            return m.group(1)
    return result["graders"][0]["name"] if result["graders"] else None


def frontier_svg(result: dict, grader: str | None, x_metric: str = "cost") -> str:
    pts = []
    for t, s in result["summary"].items():
        d = s["graders"].get(grader or "", {})
        y = d.get("mean")
        x = s["ops"]["cost_usd"]["mean"] if x_metric == "cost" else s["ops"]["latency_ms"]["p95"]
        if y is not None and x is not None:
            pts.append((t, x, y, d.get("ci_low"), d.get("ci_high")))
    if not pts:
        return '<p class="mut">No cost or latency data for a quality / cost chart (add pricing to provider profiles).</p>'
    w, h, pad = 520, 260, 44
    xmax = max(p[1] for p in pts) * 1.15 or 1
    ymin = min(0.5, min((p[3] if p[3] is not None else p[2]) for p in pts) - 0.05)

    def X(v):
        return pad + (w - 2 * pad) * (v / xmax)

    def Y(v):
        return h - pad - (h - 2 * pad) * ((v - ymin) / (1 - ymin))
    xl = "cost per case (USD)" if x_metric == "cost" else "p95 latency (ms)"
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" aria-label="quality vs {xl}">',
             f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#ccc"/><line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="#ccc"/>',
             f'<text x="{w/2}" y="{h-8}" text-anchor="middle" font-size="11" fill="#6b6b70">{escape(xl)}</text>',
             f'<text x="12" y="{h/2}" text-anchor="middle" font-size="11" fill="#6b6b70" transform="rotate(-90 12 {h/2})">{escape(grader or "")} (mean)</text>']
    for v in (0.0, xmax / 2, xmax):
        lbl = f"{v:.4f}" if x_metric == "cost" else f"{v:,.0f}"
        parts.append(f'<text x="{X(v):.1f}" y="{h-pad+14}" text-anchor="middle" font-size="10" fill="#6b6b70">{lbl}</text>')
    for v in (ymin, (ymin + 1) / 2, 1.0):
        parts.append(f'<text x="{pad-6}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="10" fill="#6b6b70">{v:.2f}</text>')
    placed: list[tuple[float, float]] = []
    for t, x, y, lo, hi in sorted(pts, key=lambda p: (p[1], -p[2])):
        if lo is not None and hi is not None:
            parts.append(f'<line x1="{X(x):.1f}" y1="{Y(lo):.1f}" x2="{X(x):.1f}" y2="{Y(hi):.1f}" stroke="#9db3e6" stroke-width="3"/>')
        near = [X(q[1]) for q in pts if abs(X(q[1]) - X(x)) < 24 and abs(Y(q[2]) - Y(y)) < 24]
        lx, ly = max(near) + 9, Y(y) - 6          # labels of clustered points start right of the cluster
        while any(abs(ly - py) < 13 and abs(lx - px) < 90 for px, py in placed):   # keep labels of nearby points apart
            ly += 14
        placed.append((lx, ly))
        parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="5" fill="#2b59c3"/>'
                     f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11">{escape(t)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _gates_table(result: dict) -> str:
    rows = []
    for g in result["gates"]:
        st = g["status"] if g["status"] != "fail" or g["severity"] == "block" else "warn"
        vals = ", ".join(f"{escape(k)} = {escape(_pct(v) if isinstance(v, float) else str(v))}" for k, v in g["values"].items())
        rows.append(f"<tr><td>{escape(g['gate'])}</td><td>{escape(g['target'])}</td><td>{_pill(st)}</td><td><code>{escape(g['rule'])}</code>"
                    f"</td><td class='mut'>{vals}{(' — ' + escape(g['message'])) if g['message'] else ''}</td></tr>")
    return "<table><tr><th>Gate</th><th>Target</th><th>Result</th><th>Rule</th><th>Values</th></tr>" + "".join(rows) + "</table>"


def _scores_table(result: dict) -> str:
    gr = [g["name"] for g in result["graders"]]
    head = "<tr><th>Grader</th>" + "".join(f"<th>{escape(t)}</th>" for t in result["summary"]) + "</tr>"
    rows = []
    for g in gr:
        cells = []
        for _t, s in result["summary"].items():
            d = s["graders"].get(g)
            if not d or d["mean"] is None:
                cells.append("<td class='mut'>–</td>")
                continue
            extra = []
            if result["repeats"] > 1 and d.get("pass_hat_k") is not None:
                extra.append(f"pass^k {d['pass_hat_k']:.2f}")
            if d.get("skipped"):
                extra.append(f"{d['skipped']} skipped")
            if d.get("errors"):
                extra.append(f"{d['errors']} errors")
            if d.get("mean_disagreement"):
                extra.append(f"judge spread {d['mean_disagreement']:.2f}")
            cells.append(f"<td class='num'><span class='k'>{d['mean']:.2f}</span> <span class='mut'>[{d['ci_low']:.2f}, {d['ci_high']:.2f}]</span>"
                         f"{_ci_bar(d)}<div class='mut' style='font-size:12px'>{escape(' · '.join(extra))}</div></td>")
        rows.append(f"<tr><td>{escape(g)}</td>{''.join(cells)}</tr>")
    ops = []
    for label, path in (("p95 latency (ms)", ("latency_ms", "p95")), ("cost / case (USD)", ("cost_usd", "mean")), ("tokens / case", ("tokens", "mean"))):
        cells = []
        for s in result["summary"].values():
            v = s["ops"][path[0]][path[1]]
            cells.append(f"<td class='num'>{'–' if v is None else (f'{v:.4f}' if 'cost' in label else f'{v:,.0f}')}</td>")
        ops.append(f"<tr><td class='mut'>{label}</td>{''.join(cells)}</tr>")
    return "<table>" + head + "".join(rows) + "".join(ops) + "</table>"


def _comparisons(result: dict) -> str:
    comps = result.get("comparisons") or {}
    if not comps:
        return ""
    rows = []
    for t, gs in comps.items():
        for g, d in gs.items():
            if not d.get("n"):
                continue
            cls = "ok" if d["significant"] and d["delta"] > 0 else "bad" if d["significant"] and d["delta"] < 0 else "skip"
            label = "better" if cls == "ok" else "worse" if cls == "bad" else "no significant change"
            rows.append(f"<tr><td>{escape(t)}</td><td>{escape(g)}</td><td class='num'>{d['delta']:+.3f}</td><td class='num'>[{d['ci_low']:+.3f}, "
                        f"{d['ci_high']:+.3f}]</td><td>{_pill('pass' if cls == 'ok' else 'fail' if cls == 'bad' else 'skip', label)}</td>"
                        f"<td class='mut'>{escape(', '.join(d.get('regressed', [])[:8]))}</td><td class='mut'>{escape(d.get('warning', ''))}</td></tr>")
    return ("<h2>Change vs baseline (paired, same cases)</h2><table><tr><th>Target</th><th>Grader</th><th>Δ mean</th><th>95% CI</th><th>Verdict</th>"
            "<th>Regressed cases</th><th>Note</th></tr>" + "".join(rows) + "</table>")


def _slices(result: dict) -> str:
    tags = sorted({t for s in result["summary"].values() for t in s["slices"]})
    if not tags:
        return ""
    gr = [g["name"] for g in result["graders"]]
    rows = []
    for t in tags:
        for target, s in result["summary"].items():
            vals = s["slices"].get(t, {})
            rows.append(f"<tr><td><span class='tag'>{escape(t)}</span></td><td>{escape(target)}</td>" + "".join(
                f"<td class='num'>{_pct(vals.get(g))}</td>" for g in gr) + "</tr>")
    return ("<h2>Slices by tag</h2><table><tr><th>Tag</th><th>Target</th>" + "".join(f"<th>{escape(g)}</th>" for g in gr) + "</tr>"
            + "".join(rows) + "</table>")


def _j(x: Any) -> str:
    return escape(x if isinstance(x, str) else json.dumps(x, indent=1, ensure_ascii=False, default=str))


def _cases(result: dict) -> str:
    scores: dict[tuple, list] = {}
    for s in result["scores"]:
        scores.setdefault((s["target"], s["case_id"], s["repeat"]), []).append(s)
    cases = {c["id"]: c for c in result["cases"]}
    out = []
    for smp in result["samples"]:
        sc = scores.get((smp["target"], smp["case_id"], smp["repeat"]), [])
        failed = any(s.get("passed") is False for s in sc)
        c = cases.get(smp["case_id"], {})
        tags = "".join(f"<span class='tag'>{escape(t)}</span>" for t in c.get("tags", []))
        pills = " ".join(_pill("skip" if s.get("skipped") else "pass" if s.get("passed") else "fail", f"{s['grader']} {_pct(s.get('value'))}") for s in sc)
        rows = []
        for s in sc:
            judges = s.get("details", {}).get("judges")
            jtxt = ""
            if judges:
                jtxt = "<div class='mut'>" + "<br>".join(escape(f"{j['judge']}: {_pct(j.get('value'))} — {j.get('reasoning') or j.get('error') or ''}") for j in judges) + "</div>"
            det = {k: v for k, v in s.get("details", {}).items() if k != "judges"}
            rows.append(f"<tr><td>{escape(s['grader'])}</td><td>{_pill('skip' if s.get('skipped') else 'pass' if s.get('passed') else 'fail', _pct(s.get('value')))}</td>"
                        f"<td>{escape(s.get('error') or s.get('reasoning') or s.get('skip_reason') or '')}{jtxt}"
                        f"{('<pre>' + _j(det) + '</pre>') if det else ''}</td></tr>")
        traj = ""
        if smp.get("trajectory"):
            traj = "<div class='k'>Trajectory</div><pre>" + escape("\n".join(
                f"{i}. [{t.get('kind', 'tool')}] {t['name']}({json.dumps(t.get('args', {}), ensure_ascii=False)})"
                + (f" -> {json.dumps(t.get('result'), ensure_ascii=False, default=str)[:300]}" if t.get("result") is not None else "")
                + (f" !! {t['error']}" if t.get("error") else "") for i, t in enumerate(smp["trajectory"], 1))) + "</pre>"
        ctx = ""
        if smp.get("context"):
            ctx = "<div class='k'>Retrieved context</div><pre>" + escape("\n\n".join(f"[{c['id']}] {c.get('text', '')[:600]}" for c in smp["context"])) + "</pre>"
        link = smp.get("metadata", {}).get("trace_url")
        meta_bits = [f"latency {smp['latency_ms']:.0f} ms" if smp.get("latency_ms") is not None else None,
                     f"${smp['cost_usd']:.4f}" if smp.get("cost_usd") is not None else None,
                     f"{smp['usage']['input_tokens'] + smp['usage']['output_tokens']} tokens" if smp.get("usage") else None,
                     ", ".join(f"{k}@{v}" for k, v in (smp.get("prompt_versions") or {}).items()) or None,
                     "cached" if smp.get("metadata", {}).get("cached") else None, smp.get("origin") if smp.get("origin") != "offline" else None]
        meta_txt = " · ".join(escape(b) for b in meta_bits if b)
        if link:
            meta_txt += f' · <a href="{escape(link)}" target="_blank" rel="noopener">open trace</a>'
        search = escape(f"{smp['case_id']} {' '.join(c.get('tags', []))} {smp.get('output') or ''}".lower()[:2000])
        out.append(
            f"<details class='case' data-target='{escape(smp['target'])}' data-fail='{int(failed)}' data-search='{search}'>"
            f"<summary>{_pill('fail' if failed else 'pass', 'FAIL' if failed else 'PASS')} <span class='k'>{escape(smp['case_id'])}</span>"
            f"{' ⚑ critical' if c.get('critical') else ''} <span class='mut'>· {escape(smp['target'])}"
            f"{(' · repeat ' + str(smp['repeat'])) if result['repeats'] > 1 else ''}</span> {tags}<div style='margin-top:4px'>{pills}</div></summary>"
            f"<div class='body'><div class='mut'>{meta_txt}</div>{('<pre class=bad>' + escape(smp['error']) + '</pre>') if smp.get('error') else ''}"
            f"<div class='grid'><div><div class='k'>Input</div><pre>{_j(smp.get('input'))}</pre></div>"
            f"<div><div class='k'>Output</div><pre>{_j(smp.get('output') or '')}</pre></div></div>{ctx}{traj}"
            f"<table><tr><th>Grader</th><th>Score</th><th>Reasoning / details</th></tr>{''.join(rows)}</table></div></details>")
    return "".join(out)


PII_SCOPE = ("pii_leak checks structured identifiers only: Aadhaar (with Verhoeff check), PAN, UPI ids, +91 phone "
             "numbers, email addresses and card numbers (Luhn). A pass does not mean the output is free of personal data: "
             "names, addresses and free-text identifiers are not detected.")


STATUS_TEXT = {"implemented": "implemented, not tested", "fixture_tested": "fixture tested", "live_tested": "live tested",
               "customer_validated": "customer validated"}


def _adapters(result: dict[str, Any]) -> str:
    ad = result.get("adapters") or []
    if not ad:
        return ""
    cls = {"implemented": "warn", "fixture_tested": "skip", "live_tested": "ok", "customer_validated": "ok"}
    rows = []
    for a in ad:
        exp = " <span class='pill warn'>experimental</span>" if a.get("experimental") else ""
        val = "; ".join((r.get("environment") or "") + (f" ({r.get('date')})" if r.get("date") else "") for r in a.get("records", []))
        rows.append(f"<tr><td>{escape(a['kind'])}</td><td><code>{escape(a['name'])}</code></td><td>{escape(a.get('cloud') or '')}</td>"
                    f"<td><span class='pill {cls.get(a['status'], 'skip')}'>{escape(STATUS_TEXT.get(a['status'], a['status']))}</span>{exp}</td>"
                    f"<td class='mut'>{escape(val)}</td></tr>")
    rows = "".join(rows)
    return ("<h2>Adapters used</h2><p class='mut'>How far each integration in this run has been proven. Fixture tested: passes the "
            "conformance kit on recorded data. Live tested / customer validated: recorded in the project's validation.yaml.</p>"
            "<table><tr><th>Kind</th><th>Adapter</th><th>Cloud</th><th>Status</th><th>Validation</th></tr>" + rows + "</table>")


def _notes(result: dict[str, Any]) -> str:
    out = []
    if any(g.get("type") == "pii_leak" for g in result.get("graders", [])):
        out.append(PII_SCOPE)
    dh = result.get("data_handling") or {}
    tok = dh.get("tokenizer") or {}
    if tok and tok.get("stable_key") is False:
        out.append("Tokens in this report use a per-run key (GENAI_EVALS_TOKEN_KEY not set), so they cannot be matched "
                   "against other runs.")
    if dh.get("on_error") == "fail":
        out.append("Grader and target errors count as failed scores (value 0).")
    return "".join(f"<p class='mut'>{escape(n)}</p>" for n in out)


def _online_banner(result: dict[str, Any]) -> str:
    if result.get("mode") != "online":
        return ""
    rows = []
    for s in result.get("sources", []):
        if "samples" not in s:
            continue
        win = s.get("window") or {}
        win_txt = ", ".join(f"{k} {v}" for k, v in win.items()) if isinstance(win, dict) else str(win)
        rows.append(f"<tr><td>{escape(s['source'])}</td><td>{escape(s['type'])}</td><td>{s['samples']} of {s.get('population', '?')}</td>"
                    f"<td>{_pct(s.get('sampling'))}</td><td>{escape(win_txt)}</td></tr>")
    table = ("<table><tr><th>Source</th><th>Type</th><th>Graded</th><th>Sampling rate</th><th>Window</th></tr>"
             + "".join(rows) + "</table>") if rows else ""
    return ("<div class='verdict' style='background:#eef4ff;color:#1f3b73'>Production monitoring: reference-free checks on "
            "sampled traffic. These scores are signals for alerting and investigation, not ground-truth quality claims: "
            "there are no reference answers, and sampled sessions only represent traffic the telemetry captured.</div>" + table)


def failure_categories(result: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """category -> target -> case ids failing any grader in that category (from the report template's failure_categories)."""
    cats = (result.get("report") or {}).get("failure_categories") or {}
    out: dict[str, dict[str, list[str]]] = {}
    for cat, graders in cats.items():
        g = set(graders)
        for t in result["summary"]:
            failed = sorted({sc["case_id"] for sc in result.get("scores", []) if sc["target"] == t and sc["grader"] in g and not sc.get("skipped")
                             and (sc.get("passed") is False or sc.get("error"))})
            out.setdefault(cat, {})[t] = failed
    return out


def _failure_table(result: dict[str, Any]) -> str:
    fc = failure_categories(result)
    if not fc:
        return ""
    targets = list(result["summary"])
    cats = (result.get("report") or {}).get("failure_categories") or {}
    n = len(result["cases"]) or 1
    rows = "".join(f"<tr><td><b>{escape(c)}</b><div class='mut'>{escape(', '.join(cats[c]))}</div></td>"
                   + "".join(f"<td class='num'>{len(fc[c][t])} <span class='mut'>({len(fc[c][t]) / n:.0%})</span></td>" for t in targets) + "</tr>"
                   for c in fc)
    return ("<h2>Failure categories</h2><p class='mut'>Cases failing at least one grader in each category (from the pack's report "
            "template). A case can fall in several categories.</p><table><tr><th>Category</th>"
            + "".join(f"<th>{escape(t)}</th>" for t in targets) + "</tr>" + rows + "</table>")


def _title(result: dict[str, Any], default: str) -> str:
    return (result.get("report") or {}).get("title") or default


def _intro(result: dict[str, Any]) -> str:
    t = (result.get("report") or {}).get("intro")
    return f"<p>{escape(t)}</p>" if t else ""


def render_report(result: dict[str, Any]) -> str:
    ok = result["passed"]
    targets = list(result["summary"])
    pg = _primary_grader(result)
    pv = result.get("prompt_versions") or {}
    pv_txt = " · ".join(f"{t}: " + ", ".join(f"{k}@{v}" for k, v in d.items()) for t, d in pv.items())
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(result['suite'])} · genai-evals</title><style>{CSS}</style></head><body><main>
<h1>{escape(_title(result, result['suite']))}</h1><div class="mut">{escape(result.get('description') or '')}</div>{_intro(result)}
<div class="mut">Run {escape(result['run_id'])} · {escape(result['created_at'][:19].replace('T', ' '))} UTC · {len(result['cases'])} cases × {len(targets)} targets × {result['repeats']} repeats
{(' · judges: ' + escape(', '.join(result['judges']))) if result.get('judges') else ''}{(' · baseline ' + escape(result['baseline'])) if result.get('baseline') else ''}
{(' · git ' + escape(result['git_sha'][:10])) if result.get('git_sha') else ''}</div>
{('<div class="mut">Prompt versions: ' + escape(pv_txt) + '</div>') if pv_txt else ''}
{_online_banner(result)}
<div class="verdict {'ok' if ok else 'bad'}">{'All blocking gates passed' if ok else 'Blocking gates failed: ' + escape(', '.join(sorted({g['gate'] + ' (' + g['target'] + ')' for g in result['gates'] if g['severity'] == 'block' and not g['passed']})))}</div>
<h2>Gates</h2>{_gates_table(result)}
<h2>Scores <span class="mut">(mean over cases, 95% bootstrap CI)</span></h2>{_scores_table(result)}{_failure_table(result)}
<h2>Quality vs cost</h2>{frontier_svg(result, pg, 'cost')}
{_comparisons(result)}{_slices(result)}
<h2>Cases</h2><div class="filters"><select id="ft" onchange="f()"><option value="">all targets</option>{''.join(f'<option>{escape(t)}</option>' for t in targets)}</select>
<select id="fs" onchange="f()"><option value="">pass + fail</option><option value="fail">failing only</option><option value="pass">passing only</option></select>
<input id="fq" placeholder="search id, tag, output" oninput="f()"></div>{_cases(result)}
{_adapters(result)}<div style="margin-top:30px">{_notes(result)}</div>
<p class="mut">Generated by genai-eval-framework. Inputs and outputs shown here were tokenised before storage when the suite has tokenize: true.</p>
</main><script>{JS}</script></body></html>"""


def render_scorecard(result: dict[str, Any]) -> str:
    ok = result["passed"]
    pg = _primary_grader(result)
    cards = []
    for t, s in result["summary"].items():
        d = s["graders"].get(pg or "", {})
        gates = [g for g in result["gates"] if g["target"] == t]
        blocked = [g for g in gates if g["severity"] == "block" and not g["passed"]]
        cost = s["ops"]["cost_usd"]["mean"]
        lat = s["ops"]["latency_ms"]["p95"]
        cards.append(f"<div class='card'><div class='k'>{escape(t)}</div><div class='big'>{_pct(d.get('mean'))}</div>"
                     f"<div class='mut'>{escape(pg or '')} · 95% CI {_pct(d.get('ci_low'))}–{_pct(d.get('ci_high'))}</div>"
                     f"<div style='margin-top:8px'>{_pill('fail' if blocked else 'pass', ('alert' if blocked else 'ok') if result.get('mode') == 'online' else ('not ready' if blocked else 'ready'))} "
                     f"<span class='mut'>{len(gates) - len(blocked)}/{len(gates)} gates</span></div>"
                     f"<div class='mut' style='margin-top:6px'>{('$%.4f / case' % cost) if cost is not None else 'cost n/a'} · "
                     f"{('%.0f ms p95' % lat) if lat is not None else 'latency n/a'} · critical {s['critical']['passed']}/{s['critical']['total']}</div></div>")
    risks = []
    for g in result["gates"]:
        if not g["passed"]:
            risks.append(f"{g['target']}: gate “{g['gate']}” {g['status']} ({g['rule']})")
    for t, s in result["summary"].items():
        if s["critical"]["failed"]:
            risks.append(f"{t}: critical cases failing — {', '.join(s['critical']['failed'][:5])}")
        for tag, vals in s["slices"].items():
            v = vals.get(pg or "")
            m = s["graders"].get(pg or "", {}).get("mean")
            if v is not None and m is not None and v < m - 0.15:
                risks.append(f"{t}: weak slice “{tag}” ({pg} {v:.2f} vs {m:.2f} overall)")
    ready_t = [t for t in result["summary"] if not any(g["target"] == t and g["severity"] == "block" and not g["passed"] for g in result["gates"])]
    not_t = [t for t in result["summary"] if t not in ready_t]
    online = result.get("mode") == "online"
    if online:
        verdict = ("No alerts: all blocking checks pass" if ok else
                   ("OK: " + escape(", ".join(ready_t)) + " · " if ready_t else "") + "Alerts: " + escape(", ".join(not_t)))
    else:
        verdict = ("Ready: all blocking quality gates pass" if ok else
                   ("Ready: " + escape(", ".join(ready_t)) + " · " if ready_t else "") + "Not ready: " + escape(", ".join(not_t)))
    risk_html = "".join(f"<li>{escape(r)}</li>" for r in risks[:3]) or "<li>No open risks against the configured gates.</li>"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Scorecard · {escape(result['suite'])}</title><style>{CSS}</style></head>
<body><main><h1>{'Production monitoring' if online else 'Is it ready?'} · {escape(_title(result, result['suite']))}</h1>{_intro(result)}<div class="mut">{len(result['cases'])} {'sampled sessions' if online else 'test cases'} · {escape(result['created_at'][:10])}
· judged by {escape(', '.join(result.get('judges') or ['deterministic graders only']))}</div>
{_online_banner(result)}
<div class="verdict {'ok' if ok else 'bad'}">{verdict}</div>
<div class="cards">{''.join(cards)}</div><h2>Quality vs cost</h2>{frontier_svg(result, pg, 'cost')}
<h2>Top risks</h2><ol>{risk_html}</ol>{_failure_table(result)}
<p class="mut">Scores are means over {'sampled sessions' if online else 'test cases'} with 95% confidence intervals; differences inside the intervals are not meaningful.</p>{_notes(result)}</main></body></html>"""
