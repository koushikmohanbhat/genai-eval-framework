"""v0.7.3: cost estimate and budget, agent connection, SME labelling, monthly projection, pack scaffold."""
import json

from genai_evals.cli import main as cli
from genai_evals.config import load_cases, load_providers, load_suite

SUITE = """
name: est
dataset:
  - {id: a, input: "What is two plus two?", reference: {contains: "4"}}
  - {id: b, input: "Name a colour", reference: {contains: "red"}}
  - {id: c, input: "Hi", turns: ["Hi", "And then?"], reference: {contains: "x"}}
targets:
  - {name: m, kind: model, profile: priced}
repeats: 2
judges: [judge-a]
graders:
  - {name: has, type: contains}
  - {name: helpful, type: rubric, rubric: "Is it helpful?"}
gates:
  - {name: g, rule: "mean(has) >= 0.0"}
"""
PROVIDERS = """
profiles:
  priced: {provider: mock, model: echo, pricing: {input_per_mtok: 1000000, output_per_mtok: 1000000}}
  judge-a: {provider: mock, model: heuristic-judge-a, options: {mode: judge}}
  real: {provider: openai, model: gpt-x, auth: {api_key_env: NOPE}, pricing: {input_per_mtok: 2.5, output_per_mtok: 10}}
"""


def _proj(tmp_path, suite=SUITE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "suite.yaml").write_text(suite)
    (tmp_path / "providers.yaml").write_text(PROVIDERS)
    return tmp_path


def test_estimate_counts_calls_and_prices_them(tmp_path):
    from genai_evals.estimate import estimate
    p = _proj(tmp_path, SUITE.replace("profile: priced", "profile: real"))
    s = load_suite(p / "suite.yaml")
    est = estimate(s, load_providers(p / "providers.yaml"), load_cases(s))
    by = {(ln["role"], ln["name"], ln["profile"]): ln for ln in est["lines"]}
    tgt = by[("target", "m", "real")]
    assert tgt["calls"] == (1 + 1 + 2) * 2                           # one call per turn, per repeat
    assert tgt["cost_usd"] == round(tgt["cost_usd"], 10) and tgt["cost_usd"] > 0
    expect = (tgt["input_tokens"] * 2.5 + tgt["output_tokens"] * 10) / 1e6
    assert abs(tgt["cost_usd"] - expect) < 1e-9
    judge = by[("judge", "helpful", "judge-a")]
    assert judge["calls"] == 3 * 2 and judge["cost_usd"] == 0.0        # mock judges cost nothing
    assert est["complete"] and est["estimated_cost_usd"] == round(tgt["cost_usd"], 4)


def test_dry_run_calls_nothing_and_budget_refuses_or_stops(tmp_path, monkeypatch, capsys):
    p = _proj(tmp_path)
    monkeypatch.chdir(p)
    assert cli(["run", "suite.yaml", "--providers", "providers.yaml", "--dry-run", "--out", "out/d"]) == 0
    out = capsys.readouterr().out
    assert "Plan: 3 case(s) x 2 repeat(s)" in out and "Estimated model cost" in out
    assert not (p / "out/d").exists()                                  # nothing ran
    # the estimate (mock = $0) lets it start; actual spend from the profile's pricing stops it
    rc = cli(["run", "suite.yaml", "--providers", "providers.yaml", "--budget-usd", "1", "--out", "out/b",
              "--no-html", "--no-cache"])
    r = json.loads((p / "out/b/result.json").read_text())
    assert r["spend"]["calls_stopped_by_budget"] > 0 and r["spend"]["model_usd"] >= 1
    assert any("budget $1.00 reached" in w for w in r["warnings"]) and rc == 1
    assert any("budget" in (x.get("error") or "") for x in r["samples"] + r["scores"])
    # an estimate over the budget refuses to start
    p2 = _proj(tmp_path / "p2", SUITE.replace("profile: priced", "profile: real"))
    monkeypatch.chdir(p2)
    assert cli(["run", "suite.yaml", "--providers", "providers.yaml", "--budget-usd", "0.000001", "--no-html"]) == 2
    assert "over --budget-usd" in capsys.readouterr().err


# ---- connect an existing agent ---------------------------------------------------------------------------------------

def _connect_suite(tmp_path, target: str):
    import shutil
    from pathlib import Path
    ex = Path(__file__).resolve().parents[1] / "examples/connect-your-agent/plain_agent.py"
    tmp_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(ex, tmp_path / "plain_agent.py")
    (tmp_path / "suite.yaml").write_text(f"""
name: connect
dataset:
  - {{id: one, input: "Status of ORD-2?", reference: {{final_state: {{}}, tool_calls: [{{name: lookup_order, args: {{order_id: ORD-2}}}}]}}}}
  - {{id: conv, input: "I want a refund", user_rules: [{{when: "Which order", say: "It's ORD-1"}}], max_turns: 3,
     reference: {{final_state: {{refunds: 1, refunded_amount: 900}},
                 context: {{use: [{{name: issue_refund, args: {{order_id: ORD-1}}, after_turn: 2}}]}}}}}}
targets:
  - {target}
graders:
  - {{name: outcome, type: final_state}}
  - {{name: calls, type: tool_args_match}}
  - {{name: ctx, type: context_retained}}
""")
    (tmp_path / "providers.yaml").write_text("profiles: {}\n")
    return tmp_path


def _scores(p):
    from genai_evals.runner import Runner
    r = Runner(load_suite(p / "suite.yaml"), load_providers(p / "providers.yaml"), use_cache=False).run()
    return {(x["case_id"], x["grader"]): x["value"] for x in r["scores"]}, r


def test_decorated_agent_runs_as_a_python_target(tmp_path):
    sc, r = _scores(_connect_suite(tmp_path, '{name: a, kind: python, callable: "plain_agent:support_agent"}'))
    assert sc[("one", "calls")] == 1.0 and sc[("conv", "outcome")] == 1.0 and sc[("conv", "ctx")] == 1.0
    conv = next(s for s in r["samples"] if s["case_id"] == "conv")
    assert conv["metadata"]["turns"] == 2 and [t["name"] for t in conv["trajectory"]] == ["lookup_order", "issue_refund"]


def test_decorated_agent_served_with_the_http_contract(tmp_path):
    import sys
    p = tmp_path / "srv"
    _connect_suite(p, "{}")
    sys.path.insert(0, str(p))
    try:
        import importlib
        mod = importlib.import_module("plain_agent")
        importlib.reload(mod)
        import genai_evals
        srv = genai_evals.serve(mod.support_agent, port=0, block=False)
        s = (p / "suite.yaml").read_text().replace(
            "  - {}", f'  - {{name: a, kind: http, options: {{url: "http://127.0.0.1:{srv.server_port}", contract: v1}}}}')
        (p / "suite.yaml").write_text(s)
        sc, r = _scores(p)
        srv.shutdown()
    finally:
        sys.path.remove(str(p))
        sys.modules.pop("plain_agent", None)
    assert sc[("one", "calls")] == 1.0 and sc[("conv", "outcome")] == 1.0 and sc[("conv", "ctx")] == 1.0


def _post(url, path, body):
    import urllib.request
    base, q = url.split("/?")
    req = urllib.request.Request(f"{base}{path}?{q}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", 0), None


def _serve(sheet):
    import threading

    from genai_evals.labelui import SheetStore, make_server
    store = SheetStore(sheet)
    srv, url = make_server(store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return store, srv, url


def test_label_sheet_xlsx_and_labelling_page(tmp_path):
    import urllib.request

    from openpyxl import load_workbook

    from genai_evals import labels as lb
    from genai_evals.calibration import read_reviews
    items = [{"case_id": f"c{i}", "target": "t", "tags": "it", "input": f"q{i}", "output": f"a{i}", "judge_passed": ""}
             for i in range(3)]
    sheet = lb.write_sheet(items, tmp_path / "labels.xlsx", ["ana"])
    ws = load_workbook(sheet).active
    assert ws.freeze_panes and ws.data_validations.dataValidation        # dropdowns for the label column
    store, srv, url = _serve(sheet)
    try:
        page = urllib.request.urlopen(url, timeout=5).read().decode()
        assert "Does the answer meet the bar?" in page
        assert urllib.request.urlopen(url.split("?")[0] + "api/rows", timeout=5).status == 200
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "code", 0) == 403                                     # no token, no access
    try:
        assert _post(url, "/api/save", {"i": 0, "values": {"label": "pass"}})[0] == 400   # sign-off needs a name
        assert _post(url, "/api/save", {"i": 0, "values": {"label": "pass", "reviewer": "ana"}})[0] == 200
        code, body = _post(url, "/api/save", {"i": 1, "values": {"label": "fail", "reviewer": "ana",
                                                                  "notes": "wrong", "judge_passed": "x"}})
        assert code == 200 and body["done"] == 2
        assert _post(url, "/api/save", {"i": 9, "values": {"label": "pass", "reviewer": "ana"}})[0] == 400
    finally:
        srv.shutdown()
    got = read_reviews(sheet)
    assert [r["label"] for r in got[("c0", "t")]] == [True] and [r["label"] for r in got[("c1", "t")]] == [False]
    assert ("c2", "t") not in got
    from genai_evals.sheets import read_rows
    assert all(r["judge_passed"] in ("", None) for r in read_rows(sheet))       # only editable columns change


def test_review_sheet_xlsx_through_the_page(tmp_path):
    from genai_evals import cases as cs
    from genai_evals import seed as sd
    from genai_evals.config import ProvidersConfig

    from .conftest import MOCK_PROVIDERS
    from .test_security_and_seed import _docs
    rows = sd.seed(sd.chunk(sd.read_docs(_docs(tmp_path))), ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=4)
    drafts, sheet = tmp_path / "drafts.jsonl", tmp_path / "review.xlsx"
    cs.write_jsonl(rows, drafts)
    assert cs.write_review_sheet(drafts, sheet) == len(rows)
    store, srv, url = _serve(sheet)
    assert store.kind == "review"
    try:
        assert _post(url, "/api/save", {"i": 0, "values": {"decision": "keep", "reviewer": "ana"}})[0] == 200
        assert _post(url, "/api/save", {"i": 1, "values": {"decision": "fix", "reviewer": "ana",
                                                           "corrected_answer": "seven days"}})[0] == 200
        assert _post(url, "/api/save", {"i": 2, "values": {"decision": "drop", "reviewer": "raj"}})[0] == 200
    finally:
        srv.shutdown()
    res = cs.apply_review_sheet(drafts, sheet)
    assert len(res["kept"]) == 1 and len(res["fixed"]) == 1 and len(res["dropped"]) == 1
    assert any(r.get("reference", {}).get("answer") == "seven days" for r in cs.read_jsonl(drafts))


def test_pack_new_scaffolds_a_partner_package_that_catches_the_vulnerable_system(tmp_path, capsys):
    import re

    import yaml

    from genai_evals import packs
    from genai_evals.config import load_providers
    from genai_evals.runner import Runner
    for base in packs.builtin():
        m = packs.manifest(packs.resolve(base))
        assert set(m["start_here"]) <= set(m["graders"]) and 3 <= len(m["start_here"]) <= 7
    assert cli(["pack", "new", "Acme Claims", "--extends", "tool-agent", "--out", str(tmp_path / "pkg")]) == 0
    assert "overlay 'acme-claims'" in capsys.readouterr().out
    pkg = tmp_path / "pkg"
    meta = (pkg / "pyproject.toml").read_text()          # tomllib is 3.11+
    assert '[project.entry-points."genai_evals.overlays"]\nacme-claims = "genai_evals_acme_claims:acme_claims"' in meta
    odir = pkg / "genai_evals_acme_claims" / "acme_claims"
    spec = yaml.safe_load((odir / "overlay.yaml").read_text())
    assert spec["pack"] == "tool-agent" and {g["name"] for g in spec["gates"]} == {"acme-claims-quality", "acme-claims-no-prompt-leak", "acme-claims-critical"}
    wf = yaml.safe_load((pkg / ".github/workflows/ci.yml").read_text())
    steps = " ".join(s.get("run", "") for s in wf["jobs"]["overlay"]["steps"])
    assert "acme-claims \\[partner\\]" in steps and "python -m build --wheel" in steps
    sed = re.search(r"sed -i 's/(.*?)/(.*?)/' targets/offline.yaml", steps)

    def run(vulnerable):
        dest = packs.scaffold("tool-agent", tmp_path / ("bad" if vulnerable else "good"), overlays=[str(odir)])
        if vulnerable:                                    # exactly what the generated workflow does
            t = dest / "targets" / "offline.yaml"
            pat = sed.group(1).replace("\\(", "(").replace("\\)", ")")
            t.write_text(re.sub(pat, sed.group(2).replace("\\1", r"\1"), t.read_text()))
            assert "vulnerable: true" in t.read_text()
        r = Runner(load_suite(dest / "suite.yaml"), load_providers(dest / "providers.yaml"), use_cache=False).run()
        return {g["gate"]: g["passed"] for g in r["gates"]}
    good, bad = run(False), run(True)
    assert good["acme-claims-quality"] and good["acme-claims-critical"]
    assert good["acme-claims-no-prompt-leak"] and not bad["acme-claims-no-prompt-leak"] and not bad["acme-claims-critical"]
    assert cli(["pack", "new", "x", "--extends", "tool-agent", "--out", str(pkg)]) == 2      # not empty
    assert cli(["pack", "new", "security-guardrails", "--extends", "tool-agent", "--out", str(tmp_path / "s")]) == 2
