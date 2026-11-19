"""v0.7.4: quickstart against an HTTP endpoint, telemetry preflight, partner overlay index."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from genai_evals.cli import main as cli
from genai_evals.config import SourcesConfig
from genai_evals.sources import preflight as pf

FIX = Path(__file__).resolve().parents[1] / "src/genai_evals/conformance/fixtures/aws_agentcore_observability.jsonl"


def _endpoint(reply, status=200):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps(reply(d)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/chat"


def _guarded(d):
    q = d.get("message", "")
    return {"data": {"reply": "Refunds take 7 days." if "refund" in q.lower() else "I help with orders and refunds."},
            "sources": [{"id": "kb-1", "text": "Refund policy"}]}


def _vulnerable(d):
    m = re.search(r"QS-[A-Z]+-\d+", d.get("message", ""))
    return {"data": {"reply": m.group(0) if m else "I help with orders and refunds."}}


def test_infer_mapping():
    from genai_evals import quickstart as qs
    opts, notes = qs.infer_mapping({"data": {"reply": "hello there", "meta": {"id": "abcdefghijklmnop"}},
                                    "tool_calls": [], "session_id": "s"})
    assert opts["map"]["output"] == "data.reply" and opts["map"]["trajectory"] == "tool_calls" and opts["map"]["session_id"] == "session_id"
    assert any("longest text field" in n for n in notes)
    assert qs.infer_mapping({"choices": [{"message": {"content": "x"}}]})[0]["map"]["output"] == "choices.0.message.content"
    assert qs.infer_mapping({"output": "x", "tool_calls": [], "state": {}})[0] == {"contract": "v1"}
    with pytest.raises(qs.ProbeError):
        qs.infer_mapping({"n": 1})


def test_quickstart_with_client_questions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GENAI_EVALS_TOKEN_KEY", "test")
    srv, url = _endpoint(_guarded)
    q = tmp_path / "q.csv"
    q.write_text("question,expected,must_contain,critical\nHow long do refunds take?,7 days,7 days,yes\n"
                 + "".join(f"Refund question {i}?,,7 days,\n" for i in range(5)))
    try:
        rc = cli(["quickstart", "--url", url, "--cases", str(q), "--out", str(tmp_path / "qs")])
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert rc == 0 and "answer: 'data.reply'" in out and "context: 'sources'" in out and "no tool calls" in out
    assert "6 client question(s) + 7 probes" in out and "none of the quickstart checks flagged" in out
    res = json.loads((tmp_path / "qs/out/first/result.json").read_text())
    gates = {g["gate"]: g["passed"] for g in res["gates"]}
    assert gates["key-facts"] and gates["no-injection-followed"] and all(g["severity"] == "warn" for g in res["gates"])
    env = (tmp_path / "qs/.env").read_text()
    assert f"SYSTEM_URL={url}" in env
    suite = (tmp_path / "qs/suite.yaml").read_text()
    assert "${SYSTEM_URL}" in suite and "127.0.0.1" not in suite        # the URL lives in .env, not the suite
    cases = [json.loads(x) for x in (tmp_path / "qs/cases.jsonl").read_text().splitlines()]
    assert cases[0]["critical"] and cases[0]["reference"] == {"answer": "7 days", "contains": ["7 days"]}


def test_quickstart_probes_only_flags_a_vulnerable_endpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GENAI_EVALS_TOKEN_KEY", "test")
    srv, url = _endpoint(_vulnerable)
    try:
        rc = cli(["quickstart", "--url", url, "--out", str(tmp_path / "qs")])
    finally:
        srv.shutdown()
    out = capsys.readouterr().out
    assert rc == 0                                         # warn-only: directional, never blocks
    assert "domain-agnostic probes only" in out and "1 check(s) flagged" in out and "no-injection-followed" in out
    assert "add the client's questions" in out


def test_quickstart_reports_probe_failures_clearly(tmp_path, capsys):
    srv, url = _endpoint(lambda d: {"detail": "bad"}, status=422)
    try:
        assert cli(["quickstart", "--url", url, "--out", str(tmp_path / "qs")]) == 2
    finally:
        srv.shutdown()
    assert "HTTP 422" in capsys.readouterr().err
    assert cli(["quickstart", "--url", "http://127.0.0.1:9/x", "--out", str(tmp_path / "q2")]) == 2
    assert cli(["quickstart", "--url", url, "--header", "Authorization=NOT_SET_XYZ"]) == 2


# ---- preflight --------------------------------------------------------------------------------------------------------

class Denied(Exception):
    def __init__(self, op):
        super().__init__(f"An error occurred (AccessDeniedException) when calling the {op} operation")
        self.response = {"Error": {"Code": "AccessDeniedException"}}


class FakeAWS:
    def __init__(self, logging=None, events=None, deny=None):
        self.logging, self.events, self.deny = logging, events or [], deny or set()

    def get_model_invocation_logging_configuration(self):
        if "bedrock" in self.deny:
            raise Denied("GetModelInvocationLoggingConfiguration")
        return {"loggingConfig": self.logging} if self.logging else {}

    def filter_log_events(self, **kw):
        if "logs" in self.deny:
            raise Denied("FilterLogEvents")
        return {"events": [{"message": json.dumps(e)} for e in self.events]}


def _cfg(*sources):
    return SourcesConfig(sources=list(sources), base_dir=".")


def _bedrock(client):
    return pf.check_one({"name": "inv", "type": "aws_bedrock_invocation_logs", "region": "ap-south-1",
                         "log_group": "/aws/bedrock/modelinvocations"}, ".", client)


def test_preflight_bedrock_logging_states():
    assert _bedrock(FakeAWS()).status == "missing"
    f = _bedrock(FakeAWS(logging={"textDataDeliveryEnabled": False}))
    assert f.status == "off" and "textDataDeliveryEnabled" in f.fix and f.fallbacks
    f = _bedrock(FakeAWS(logging={"textDataDeliveryEnabled": True, "cloudWatchConfig": {"logGroupName": "/other"}}))
    assert f.status == "missing" and "log_group: /other" in f.fix
    f = _bedrock(FakeAWS(deny={"bedrock"}))
    assert f.status == "denied" and "bedrock:GetModelInvocationLoggingConfiguration" in f.fix
    on = {"textDataDeliveryEnabled": True, "cloudWatchConfig": {"logGroupName": "/aws/bedrock/modelinvocations"}}
    assert _bedrock(FakeAWS(logging=on)).status == "missing"                       # on, but nothing in the window
    assert _bedrock(FakeAWS(logging=on, deny={"logs"})).status == "denied"
    rec = {"modelId": "m", "requestId": "r1", "timestamp": "2026-11-19T10:00:00Z",
           "input": {"inputBodyJson": {"messages": [{"role": "user", "content": [{"text": "hi"}]}]}},
           "output": {"outputBodyJson": {"output": {"message": {"content": [{"text": "hello"}]}}}}}
    assert _bedrock(FakeAWS(logging=on, events=[rec])).status == "ok"


def test_preflight_spans_without_content_and_the_cli(tmp_path, capsys):
    spans = [json.loads(json.loads(x)["message"]) for x in FIX.read_text().splitlines() if x.strip()]
    for s in spans:
        for k in [k for k in s.get("attributes", {}) if "messages" in k or "completion" in k or "prompt." in k[:7]]:
            s["attributes"].pop(k)
    spec = {"name": "spans", "type": "aws_agentcore_observability", "region": "ap-south-1"}
    found = pf.preflight(_cfg(spec), {"spans": FakeAWS(events=spans)})
    assert found[0].status == "off" and "CAPTURE_MESSAGE_CONTENT" in found[0].fix
    full = [json.loads(json.loads(x)["message"]) for x in FIX.read_text().splitlines() if x.strip()]
    assert pf.preflight(_cfg(spec), {"spans": FakeAWS(events=full)})[0].status == "ok"
    assert pf.preflight(_cfg(spec), {"spans": FakeAWS(deny={"logs"})})[0].status == "denied"
    text = pf.render(found)
    assert "can't turn payload logging on" in text and "@agent" in text and "otlp" in text
    (tmp_path / "s.yaml").write_text("sources:\n  - {name: f, type: otlp, fixture: f.jsonl}\n")
    assert cli(["sources", "preflight", str(tmp_path / "s.yaml")]) == 0
    assert "recorded fixture" in capsys.readouterr().out


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class FakeBQ:
    def __init__(self, err=None, rows=None):
        self.err, self.rows = err, rows or []

    def get_table(self, t):
        if self.err:
            raise self.err(f"404 Not found: Table {t}" if self.err is NotFound else "403 Access Denied")

        class T:
            schema = [{"name": "full_request"}, {"name": "full_response"}, {"name": "logging_time"}]
        return T()


def test_preflight_vertex_table():
    spec = {"name": "rr", "type": "gcp_vertex_request_response", "table": "bq://p.d.t", "project": "p"}
    assert pf.check_one(spec, ".", FakeBQ(NotFound)).status == "missing"
    f = pf.check_one(spec, ".", FakeBQ(Forbidden))
    assert f.status == "denied" and "roles/bigquery.dataViewer" in f.fix
    assert pf.classify(RuntimeError("weird")) == "unknown"


# ---- partner overlays: index and contribution bar ---------------------------------------------------------------------

def test_overlay_index_and_contribution_bar(tmp_path, monkeypatch, capsys):
    from genai_evals import packs
    monkeypatch.setenv("GENAI_EVALS_TOKEN_KEY", "test")
    assert packs.check_index() == []
    assert any("missing" in p for p in packs.check_index([{"name": "x", "extends": "tool-agent"}]))
    assert any("not a built-in" in p for p in packs.check_index([{**packs.overlay_index()[0], "name": "y", "extends": "nope"}]))
    assert cli(["pack", "list", "--available"]) == 0 and "sap-order-tools" in capsys.readouterr().out
    sap = Path(__file__).resolve().parents[1] / "examples/partner-pack-sap/acme_evals_sap/sap_order_tools"
    assert all(c.ok for c in packs.check_overlay(sap))
    assert cli(["pack", "new", "retail-returns", "--extends", "tool-agent", "--out", str(tmp_path / "pkg")]) == 0
    od = tmp_path / "pkg/genai_evals_retail_returns/retail_returns"
    got = {c.name: c.ok for c in packs.check_overlay(od)}
    assert got["metadata"] is False and got["catches the vulnerable system"] and got["passes on the guarded system"]
    y = od / "overlay.yaml"
    y.write_text(y.read_text().replace('"TODO: one line on the domain this adds (systems, rules, attacks)"', "Retail returns"))
    rows = [json.loads(x) for x in (od / "cases.jsonl").read_text().splitlines()]
    rows[0]["input"] += " My card is 4111 1111 1111 1111."
    (od / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = {c.name: c.ok for c in packs.check_overlay(od, run=False)}
    assert got["metadata"] and got["no real-looking identifiers"] is False
    assert "pack check" in (tmp_path / "pkg/.github/workflows/ci.yml").read_text()


def test_live_walkthrough_prompt_edit_matches_the_pack_prompt():
    root = Path(__file__).resolve().parents[1]
    script = (root / "examples/live-walkthrough/walkthrough.sh").read_text()
    pat = re.search(r"sed -i\.bak 's/\^(Refunds:.*?)/Refunds: only for delivered orders\./'", script).group(1)
    line = pat.replace("\\.", ".")
    prompt = (root / "packs/tool-agent/prompts/support_agent/1.0.0/system.md.j2").read_text()
    assert line in prompt.splitlines()                   # the walkthrough's edit still applies to the pack prompt
