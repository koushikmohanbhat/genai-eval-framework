"""v0.7.2: multi-turn conversations on every agent target, simulated users, context drift."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from genai_evals import packs
from genai_evals.config import load_providers, load_suite
from genai_evals.models import Case
from genai_evals.runner import Runner


def _run(dest):
    return Runner(load_suite(dest / "suite.yaml"), load_providers(dest / "providers.yaml"), use_cache=False).run()


def _mt(r):
    return {s["case_id"]: s for s in r["samples"] if s["case_id"].startswith("mt-") and s["repeat"] == 0}


def test_pack_conversations_are_driven_turn_by_turn(tmp_path):
    r = _run(packs.scaffold("tool-agent", tmp_path / "p"))
    mt = _mt(r)
    assert len(mt) == 6
    assert mt["mt-01"]["metadata"]["turns"] == 2                       # the rules user answered the agent's question
    assert [m["role"] for m in mt["mt-05"]["messages"]] == ["user", "assistant"] * 4
    assert {t["turn"] for t in mt["mt-05"]["trajectory"]} == {1, 2, 3}  # tool calls tagged with their turn
    assert len(mt["mt-05"]["metadata"]["turn_latency_ms"]) == 4
    gates = {g["gate"]: g for g in r["gates"]}
    assert gates["multi-turn-context"]["passed"] and gates["efficient"]["passed"]   # step budgets apply per turn


def test_context_drift_is_caught(tmp_path):
    dest = packs.scaffold("tool-agent", tmp_path / "forgetful")
    t = dest / "targets" / "offline.yaml"
    t.write_text(t.read_text().replace('callable: "sim_system:agent"}', 'callable: "sim_system:agent", options: {forgetful: true}}'))
    r = _run(dest)
    ctx = {s["case_id"]: s for s in r["scores"] if s["grader"] == "context_retained" and s["repeat"] == 0}
    assert ctx["mt-02"]["value"] == 0.0 and ctx["mt-02"]["details"]["missing"]      # acted on the corrected order
    assert ctx["mt-03"]["value"] == 0.0                                              # lost "it"
    assert not {g["gate"]: g for g in r["gates"]}["multi-turn-context"]["passed"]


def test_rules_simulator_reacts_to_the_agent_and_fires_once():
    import asyncio

    from genai_evals.models import Message
    from genai_evals.targets.simulator import RulesSimulator
    sim = RulesSimulator([{"when": "order number", "say": "ORD-1"}, {"when": "anything else", "say": "No thanks"},
                          {"otherwise": "[DONE]"}])
    c = Case(id="c", input="hi")
    ask = [Message(role="assistant", content="Which order number?")]
    assert asyncio.run(sim.next(c, ask, 1)) == "ORD-1"
    assert asyncio.run(sim.next(c, ask, 2)) is None                                 # fired once, then otherwise -> end
    assert asyncio.run(sim.next(c, [Message(role="assistant", content="Anything else?")], 3)) == "No thanks"


class _Agent(BaseHTTPRequestHandler):
    sessions: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        sid = body.get("session_id") or "new"
        key = sid if sid.startswith("srv-") else "srv-" + sid[-6:]          # the server's own session id
        mem = self.sessions.setdefault(key, [])
        mem.append(body["input"])
        reply = "Which order?" if len(mem) == 1 else f"Refunded {body['input']}"
        out = {"output": reply, "session": key, "calls": [] if len(mem) == 1 else
               [{"name": "issue_refund", "args": {"order_id": body["input"]}}], "turn_seen": body.get("turn")}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_http_target_holds_a_session_across_turns(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _Agent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    (tmp_path / "suite.yaml").write_text(f"""
name: http-mt
dataset:
  - {{id: h1, input: "I want a refund", user_rules: [{{when: "Which order", say: "ORD-7"}}], max_turns: 3,
     reference: {{context: {{use: [{{name: issue_refund, args: {{order_id: ORD-7}}, after_turn: 2}}]}}}}}}
targets:
  - name: agent
    kind: http
    options:
      url: "http://127.0.0.1:{srv.server_port}/chat"
      conversation_body: {{input: "{{{{ message }}}}", session_id: "{{{{ session_id }}}}", turn: "{{{{ turn }}}}"}}
      map: {{output: output, trajectory: calls, session_id: session}}
graders:
  - {{name: ctx, type: context_retained}}
""")
    (tmp_path / "providers.yaml").write_text("profiles: {}\n")
    r = Runner(load_suite(tmp_path / "suite.yaml"), load_providers(tmp_path / "providers.yaml"), use_cache=False).run()
    srv.shutdown()
    s = r["samples"][0]
    assert s["metadata"]["turns"] == 2 and s["output"] == "Refunded ORD-7"
    assert s["session_id"].startswith("srv-")                     # the server's session id is used from turn 2
    assert any(len(v) == 2 for v in _Agent.sessions.values())       # both turns reached the same server session
    assert r["scores"][0]["value"] == 1.0


class _FakeAgentCore:
    def __init__(self):
        self.calls = []

    def invoke_agent_runtime(self, **kw):
        import io
        self.calls.append(kw)
        p = json.loads(kw["payload"])
        return {"response": io.BytesIO(json.dumps({"result": f"echo {p['prompt']}"}).encode()),
                "runtimeSessionId": kw["runtimeSessionId"]}


class _FakeBedrockAgent:
    def __init__(self):
        self.sessions = []

    def invoke_agent(self, **kw):
        self.sessions.append(kw["sessionId"])
        return {"completion": [{"chunk": {"bytes": f"ok {kw['inputText']}".encode()}},
                               {"trace": {"trace": {"orchestrationTrace": {"invocationInput": {"actionGroupInvocationInput": {
                                   "function": "lookup", "parameters": [{"name": "q", "value": kw["inputText"]}]}}}}}}]}


class _FakeAgentEngine:
    def __init__(self):
        self.created, self.queries = 0, []

    def create_session(self, user_id):
        self.created += 1
        return {"id": "ae-sess-1"}

    def stream_query(self, **kw):
        self.queries.append(kw)
        return [{"content": {"parts": [{"text": f"got {kw['message']}"}]}}]


@pytest.mark.parametrize("framework,client_key,fake", [
    ("agentcore_runtime", "bedrock-agentcore", _FakeAgentCore),
    ("bedrock_agent", "bedrock-agent-runtime", _FakeBedrockAgent),
    ("agent_engine", "agent_engine", _FakeAgentEngine),
])
def test_managed_agent_targets_reuse_one_session(tmp_path, monkeypatch, framework, client_key, fake):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: None))
    from genai_evals.config import ProvidersConfig, TargetSpec
    from genai_evals.targets.base import RunContext, build_target
    (tmp_path / "suite.yaml").write_text("name: x\ndataset: []\n")
    suite = load_suite(tmp_path / "suite.yaml")
    f = fake()
    ctx = RunContext(suite=suite, providers=ProvidersConfig(), pool=None, clients={client_key: f})
    opts = {"agent_arn": "arn", "agent_id": "a", "agent_alias_id": "b", "resource": "r"}
    t = build_target(TargetSpec(name="t", kind="agent", framework=framework, options=opts), ctx)
    import asyncio
    s = asyncio.run(t.execute(Case(id="c1", input="first", turns=["first", "second", "third"]), 0))
    assert s.metadata["turns"] == 3 and s.output.endswith("third")
    if framework == "agentcore_runtime":
        assert len({c["runtimeSessionId"] for c in f.calls}) == 1 and len(f.calls) == 3
        assert len(f.calls[0]["runtimeSessionId"]) >= 33                  # AgentCore's minimum session id length
    elif framework == "bedrock_agent":
        assert len(set(f.sessions)) == 1 and len(f.sessions) == 3
        assert [tc.turn for tc in s.trajectory] == [1, 2, 3]
    else:
        assert f.created == 1 and {q["session_id"] for q in f.queries} == {"ae-sess-1"}


# ---- cases mutate --------------------------------------------------------------------------------------------------

def test_mutate_targets_real_tools_and_marks_every_variant(tmp_path):
    import base64

    from genai_evals import mutate as mu
    from genai_evals.config import load_cases
    dest = packs.scaffold("tool-agent", tmp_path / "p", overlays=["security-guardrails"])
    suite = load_suite(dest / "suite.yaml")
    rows, rep = mu.mutate(suite, load_cases(suite), per_case=4)
    assert rep["targeted_tools"] == ["issue_refund", "update_address"]         # not reads, not hand-offs to a person
    assert rep["seed_cases"] >= 8 and len(rows) == 4 * rep["seed_cases"]
    assert set(rep["by_strategy"]) >= set(mu.STRATEGIES) and all(rep["by_strategy"][s] for s in mu.STRATEGIES)
    for r in rows:
        m = r["metadata"]["marker"]
        text = " ".join([r["input"], *(r.get("turns") or [])])
        if r["metadata"]["strategy"] == "base64":
            text += base64.b64decode(text.rsplit(" ", 1)[-1]).decode()
        assert m in text and m in r["reference"]["injection_markers"]
        assert r["review_status"] == "draft" and r["critical"] and "mutated" in r["tags"]
    tt = [r for r in rows if r["metadata"]["strategy"] == "tool_targeted"]
    assert tt and all(r["metadata"]["targets_tool"] in r["input"] for r in tt)
    assert all(len(r["turns"]) == 2 for r in rows if r["metadata"]["strategy"] == "split_turns")


def test_mutated_extraction_attacks_run_and_a_guarded_system_resists_them(tmp_path, monkeypatch):
    from genai_evals import cases as cs
    from genai_evals.cli import main as cli
    dest = packs.scaffold("structured-extraction", tmp_path / "x", overlays=["security-guardrails"])
    monkeypatch.chdir(dest)
    assert cli(["cases", "mutate", "suite.yaml", "--out", "drafts/mut.jsonl", "--no-tokenize"]) == 0
    rows = [json.loads(x) for x in (dest / "drafts/mut.jsonl").read_text().splitlines()]
    assert not any(r.get("turns") for r in rows)                    # no split_turns for documents
    promoted = dest / "data/mutations.jsonl"
    ok, refused = cs.promote(dest / "drafts/mut.jsonl", promoted, reviewer="test", schema=dest / "schema/case.schema.json")
    assert len(ok) == len(rows) and not refused
    s = dest / "suite.yaml"
    s.write_text(s.read_text().replace("extra_datasets: [", "extra_datasets: [data/mutations.jsonl, ", 1)
                 if "extra_datasets: [" in s.read_text() else s.read_text() + "extra_datasets: [data/mutations.jsonl]\n")
    r = _run(dest)
    inj = [x for x in r["scores"] if x["grader"] == "injection_resisted" and x["case_id"].startswith("mut-")
           and x["value"] is not None]                  # the grader applies to indirect_injection variants
    assert len(inj) >= 8 and all(x["value"] == 1.0 for x in inj)


# ---- usage export --------------------------------------------------------------------------------------------------

def test_usage_export_has_no_customer_data_and_hashes_custom_names(tmp_path, monkeypatch, capsys):
    from genai_evals.cli import main as cli
    proj = packs.scaffold("structured-extraction", tmp_path / "acme-claims-project")
    s = proj / "suite.yaml"
    s.write_text(s.read_text().replace("gates:\n", 'gates:\n  - {name: acme-secret-gate, rule: "mean(fields) >= 0.0"}\n', 1))
    monkeypatch.chdir(proj)
    assert cli(["run", "suite.yaml", "--profile", "quickstart", "--out", "out/q", "--no-html"]) == 0
    assert cli(["run", "suite.yaml", "--out", "out/full", "--no-html", "--allow-missing-baseline"]) in (0, 1)
    capsys.readouterr()
    assert cli(["usage", "export", "out", "--out", "usage.json", "--salt", "k"]) == 0
    out = capsys.readouterr().out
    assert "Nothing was sent anywhere" in out
    doc = json.loads((proj / "usage.json").read_text())
    text = json.dumps(doc)
    results = [json.loads(p.read_text()) for p in (proj / "out").rglob("result.json")]
    for r in results:
        for c in r["cases"]:
            assert c["id"] not in text
        for smp in r["samples"]:
            assert (smp.get("output") or "")[:30] not in text or not smp.get("output")
        assert r["run_id"] not in text
    assert "acme-secret-gate" not in text and "gate:" in text                      # custom gate counted, not named
    assert "acme-claims-project" not in text and str(tmp_path) not in text
    assert {r["profile"] for r in doc["runs"]} == {"quickstart", None}
    assert all(r["pack"].startswith("structured-extraction@") for r in doc["runs"])
    assert all(r["cases"] in ("1-29", "30-99", "100-299", "300-999", "1000+") for r in doc["runs"])
    # stable hashes with the same salt, different with another
    assert cli(["usage", "export", "out", "--out", "u2.json", "--salt", "k"]) == 0
    assert json.loads((proj / "u2.json").read_text())["project"] == doc["project"]
    assert cli(["usage", "export", "out", "--out", "u3.json", "--salt", "other"]) == 0
    assert json.loads((proj / "u3.json").read_text())["project"] != doc["project"]
    assert cli(["usage", "schema"]) == 0 and "never" in capsys.readouterr().out
