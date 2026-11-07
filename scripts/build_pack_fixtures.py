"""Build each pack's recorded telemetry (fixtures/telemetry.otlp.json) from its simulator, so `genai-evals online`
runs offline on realistic OTLP spans. A few production-style issues are injected so the monitoring report has
something to show. Run after changing a pack's simulator or starter cases:

    python scripts/build_pack_fixtures.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
T0 = 1793869200_000_000_000          # 2026-11-05T09:00:00Z in ns


def _attr(k, v):
    return {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": str(v)}}


def _span(tid, sid, name, start, end, attrs, parent=None):
    d = {"traceId": tid, "spanId": sid, "name": name, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
         "attributes": [_attr(k, v) for k, v in attrs.items()]}
    if parent:
        d["parentSpanId"] = parent
    return d


def trace(i, service, question, answer, latency_ms, context=(), tools=(), tokens=(60, 25)):
    tid, t0 = f"{i + 1:032x}", T0 + i * 90_000_000_000
    end = t0 + int(latency_ms * 1e6)
    spans = [_span(tid, "a1", f"invoke_agent {service}", t0, end, {"gen_ai.operation.name": "invoke_agent", "session.id": f"sess-{i:04d}"})]
    if context:
        attrs = {"openinference.span.kind": "RETRIEVER"}
        for n, c in enumerate(context):
            attrs[f"retrieval.documents.{n}.document.id"] = c["id"]
            attrs[f"retrieval.documents.{n}.document.content"] = c["text"]
        spans.append(_span(tid, "a2", "retrieve", t0 + 50_000_000, t0 + 250_000_000, attrs, "a1"))
    for n, (name, args) in enumerate(tools):
        spans.append(_span(tid, f"t{n}", f"execute_tool {name}", t0 + (100 + n * 50) * 1_000_000, t0 + (140 + n * 50) * 1_000_000,
                           {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name, "gen_ai.tool.call.arguments": json.dumps(args)}, "a1"))
    spans.append(_span(tid, "a3", "chat model", t0 + 300_000_000, end - 10_000_000,
                       {"openinference.span.kind": "LLM", "llm.input_messages.0.message.role": "user", "llm.input_messages.0.message.content": question,
                        "llm.output_messages.0.message.role": "assistant", "llm.output_messages.0.message.content": answer,
                        "llm.token_count.prompt": tokens[0], "llm.token_count.completion": tokens[1]}, "a1"))
    return {"resourceSpans": [{"resource": {"attributes": [_attr("service.name", service)]}, "scopeSpans": [{"spans": spans}]}]}


def load_sim(pack: Path):
    spec = importlib.util.spec_from_file_location(f"sim_{pack.name.replace('-', '_')}", pack / "sim_system.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(pack))
    spec.loader.exec_module(mod)
    return mod


def cases(pack: Path):
    for line in (pack / "data" / "starter.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            yield SimpleNamespace(id=c["id"], input=c["input"], reference=c.get("reference", {}),
                                  input_text=lambda c=c: c["input"] if isinstance(c["input"], str) else json.dumps(c["input"]))


def build_rag(pack: Path) -> list:
    sim, docs = load_sim(pack), []
    for i, c in enumerate(cases(pack)):
        r = sim.answer(c, {"repeat": 0})
        out = r["output"]
        if c.id == "kb-q06":     # production issue: answer adds a claim the passages don't support
            out += " Late claims are always accepted if your manager agrees."
        if c.id == "kb-q12":     # production issue: answer echoes a personal email address
            out += " You can also ask priya.sharma@example.com."
        docs.append(trace(i, "knowledge-assistant", c.input_text(), out, r["latency_ms"] + (2600 if c.id == "kb-q03" else 0), r["context"]))
    return docs


def build_agent(pack: Path) -> list:
    sim, docs = load_sim(pack), []
    for i, c in enumerate(cases(pack)):
        r = sim.agent(c, {"repeat": 0})
        out = r["output"]
        if c.id == "sup-03":     # production issue: a delivery promise
            out += " It will definitely arrive by tomorrow."
        tools = [(t["name"], t["args"]) for t in r["trajectory"]]
        docs.append(trace(i, "support-agent", c.input_text(), out, r["latency_ms"], tools=tools))
    return docs


def build_extraction(pack: Path) -> list:
    sim, docs = load_sim(pack), []
    for i, c in enumerate(cases(pack)):
        r = sim.extract(c, {"repeat": 0})
        out = r["output"]
        if c.id == "inv-04":     # production issue: the model returned prose around the JSON
            out = "Here is the extracted invoice: " + out
        docs.append(trace(i, "invoice-extractor", c.input_text(), out, r["latency_ms"], tokens=(400, 80)))
    return docs


BUILDERS = {"rag-knowledge-assistant": build_rag, "tool-agent": build_agent, "structured-extraction": build_extraction}


def main() -> int:
    for name, fn in BUILDERS.items():
        pack = ROOT / "packs" / name
        if not (pack / "sim_system.py").exists():
            continue
        out = pack / "fixtures" / "telemetry.otlp.json"
        out.parent.mkdir(exist_ok=True)
        docs = fn(pack)
        out.write_text(json.dumps(docs, indent=None, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{name}: {len(docs)} traces -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
