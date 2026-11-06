"""Normalise telemetry from any backend into Samples, using the OpenTelemetry GenAI semantic conventions as the pivot.

Accepted span shapes: OTLP JSON (resourceSpans / attribute lists), CloudWatch `aws/spans` records (AgentCore Observability,
Transaction Search), X-Ray segments, Cloud Trace spans (labels), OpenAI Agents SDK exported spans, Langfuse observations.
Accepted attribute vocabularies: OTel GenAI (old and new message conventions), OpenInference, AgentCore / ADK span names.
The normaliser is tolerant: missing attributes produce missing fields, never errors.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from ..models import ContextChunk, Message, Sample, ToolCall, Usage

LLM_OPS = {"chat", "text_completion", "generate_content", "completion", "invoke_model", "converse", "embeddings"}
TOOL_OPS = {"execute_tool", "tool"}
AGENT_OPS = {"invoke_agent", "create_agent"}


# ---------------------------------------------------------------------------
# attribute helpers
# ---------------------------------------------------------------------------
def _otlp_value(v: dict[str, Any]) -> Any:
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_otlp_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {x["key"]: _otlp_value(x.get("value", {})) for x in v["kvlistValue"].get("values", [])}
    return None


def _attrs(a: Any) -> dict[str, Any]:
    if a is None:
        return {}
    if isinstance(a, list):     # OTLP: [{key, value: {stringValue}}]
        return {x["key"]: _otlp_value(x.get("value", {})) for x in a if isinstance(x, dict) and "key" in x}
    if isinstance(a, dict):
        return dict(a)
    return {}


def _jl(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "[{":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return v
    return v


def _ns(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e17:
            return int(x)                   # ns
        if x > 1e14:
            return int(x * 1e3)             # us
        if x > 1e11:
            return int(x * 1e6)             # ms
        return int(x * 1e9)                 # s
    if isinstance(v, str):
        if v.isdigit():
            return _ns(int(v))
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1e9)
        except ValueError:
            return None
    if isinstance(v, dict) and "seconds" in v:
        return int(v["seconds"]) * 10**9 + int(v.get("nanos", 0))
    return None


def to_span(d: dict[str, Any]) -> dict[str, Any]:
    """Any supported span-like record -> canonical {trace_id, span_id, parent_id, name, start_ns, end_ns, attrs, events, resource}."""
    if isinstance(d.get("message"), str) and d["message"].lstrip().startswith("{"):     # raw CloudWatch log event
        inner = json.loads(d["message"])
        return to_span(inner)
    if d.get("object") == "trace.span" or "span_data" in d:                             # OpenAI Agents SDK export
        return _from_openai_span(d)
    if "traceId" in d and ("type" in d and d.get("type") in {"GENERATION", "SPAN", "EVENT", "TOOL", "AGENT", "RETRIEVER"}):
        return _from_langfuse(d)
    if "trace_id" in d and "start_time" in d and "id" in d and "name" in d and "spanId" not in d:  # X-Ray segment
        a = {**(d.get("annotations") or {}), **{f"metadata.{k}": v for k, v in (d.get("metadata") or {}).get("default", {}).items()}}
        return {"trace_id": d["trace_id"], "span_id": d["id"], "parent_id": d.get("parent_id"), "name": d["name"],
                "start_ns": _ns(d.get("start_time")), "end_ns": _ns(d.get("end_time")), "attrs": {k: _jl(v) for k, v in a.items()},
                "events": [], "resource": {}}
    attrs = _attrs(d.get("attributes")) or _attrs(d.get("labels"))
    res = _attrs((d.get("resource") or {}).get("attributes")) if isinstance(d.get("resource"), dict) else {}
    events = [{"name": e.get("name"), "attrs": _attrs(e.get("attributes")), "time_ns": _ns(e.get("timeUnixNano") or e.get("time"))}
              for e in d.get("events", []) or []]
    return {"trace_id": d.get("traceId") or d.get("trace_id") or attrs.get("trace_id"), "span_id": d.get("spanId") or d.get("span_id"),
            "parent_id": d.get("parentSpanId") or d.get("parent_span_id") or d.get("parentId") or None,
            "name": d.get("name") or ((d.get("displayName") or {}).get("value") if isinstance(d.get("displayName"), dict) else "") or "",
            "start_ns": _ns(d.get("startTimeUnixNano") or d.get("start_time_unix_nano") or d.get("startTime") or d.get("start_time")),
            "end_ns": _ns(d.get("endTimeUnixNano") or d.get("end_time_unix_nano") or d.get("endTime") or d.get("end_time")),
            "attrs": attrs, "events": events, "resource": res}


def _from_openai_span(d: dict[str, Any]) -> dict[str, Any]:
    sd = d.get("span_data") or {}
    typ = sd.get("type")
    a: dict[str, Any] = {"openai.span_type": typ}
    if typ in ("generation", "response"):
        a["gen_ai.operation.name"] = "chat"
        a["gen_ai.request.model"] = sd.get("model")
        if sd.get("input") is not None:
            a["gen_ai.input.messages"] = sd.get("input")
        if sd.get("output") is not None:
            a["gen_ai.output.messages"] = sd.get("output")
        u = sd.get("usage") or {}
        a["gen_ai.usage.input_tokens"] = u.get("input_tokens") or u.get("prompt_tokens") or 0
        a["gen_ai.usage.output_tokens"] = u.get("output_tokens") or u.get("completion_tokens") or 0
    elif typ == "function":
        a.update({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": sd.get("name"), "gen_ai.tool.call.arguments": sd.get("input"),
                  "gen_ai.tool.call.result": sd.get("output")})
    elif typ == "handoff":
        a.update({"handoff.from": sd.get("from_agent"), "handoff.to": sd.get("to_agent")})
    elif typ == "agent":
        a.update({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": sd.get("name")})
    elif typ == "guardrail":
        a.update({"guardrail.name": sd.get("name"), "guardrail.action": "INTERVENED" if sd.get("triggered") else "NONE"})
    elif typ == "custom":
        a.update(sd.get("data") or {})
    return {"trace_id": d.get("trace_id"), "span_id": d.get("id"), "parent_id": d.get("parent_id"), "name": sd.get("name") or typ or "span",
            "start_ns": _ns(d.get("started_at")), "end_ns": _ns(d.get("ended_at")), "attrs": a, "events": [], "resource": {}}


def _from_langfuse(d: dict[str, Any]) -> dict[str, Any]:
    typ = d.get("type")
    a: dict[str, Any] = {k: v for k, v in (d.get("metadata") or {}).items() if isinstance(k, str)}
    if typ == "GENERATION":
        a.update({"gen_ai.operation.name": "chat", "gen_ai.request.model": d.get("model"), "gen_ai.input.messages": d.get("input"),
                  "gen_ai.output.messages": d.get("output")})
        u = d.get("usage") or d.get("usageDetails") or {}
        a["gen_ai.usage.input_tokens"] = u.get("input") or u.get("promptTokens") or 0
        a["gen_ai.usage.output_tokens"] = u.get("output") or u.get("completionTokens") or 0
        if d.get("promptName"):
            a["prompt.id"], a["prompt.version"] = d["promptName"], str(d.get("promptVersion"))
    elif typ == "TOOL" or (typ == "SPAN" and (d.get("metadata") or {}).get("type") == "tool"):
        a.update({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": d.get("name"), "gen_ai.tool.call.arguments": d.get("input"),
                  "gen_ai.tool.call.result": d.get("output")})
    elif typ == "RETRIEVER":
        a.update({"openinference.span.kind": "RETRIEVER", "retrieval.documents": d.get("output")})
    else:
        a.update({"input.value": d.get("input"), "output.value": d.get("output")})
    return {"trace_id": d.get("traceId"), "span_id": d.get("id"), "parent_id": d.get("parentObservationId"), "name": d.get("name") or typ,
            "start_ns": _ns(d.get("startTime")), "end_ns": _ns(d.get("endTime")), "attrs": a, "events": [], "resource": {}}


def expand_otlp(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """OTLP JSON export ({resourceSpans:[{resource, scopeSpans:[{spans}]}]}) -> flat span dicts carrying their resource."""
    out = []
    for rs in doc.get("resourceSpans", []):
        res = rs.get("resource", {})
        for ss in rs.get("scopeSpans", []) or rs.get("instrumentationLibrarySpans", []):
            for sp in ss.get("spans", []):
                out.append({**sp, "resource": res})
    return out


# ---------------------------------------------------------------------------
# span classification
# ---------------------------------------------------------------------------
def _op(sp: dict) -> str:
    a = sp["attrs"]
    op = str(a.get("gen_ai.operation.name") or "").lower()
    if op:
        return op
    kind = str(a.get("openinference.span.kind") or "").upper()
    if kind == "LLM":
        return "chat"
    if kind == "TOOL":
        return "execute_tool"
    if kind == "RETRIEVER":
        return "retrieve"
    if kind == "AGENT":
        return "invoke_agent"
    n = (sp.get("name") or "").lower()
    for prefix, o in (("execute_tool", "execute_tool"), ("chat", "chat"), ("invoke_agent", "invoke_agent"), ("bedrock runtime.converse", "chat"),
                      ("bedrock runtime.invokemodel", "chat"), ("generate_content", "chat"), ("call_llm", "chat"), ("execute_tool ", "execute_tool")):
        if n.startswith(prefix):
            return o
    if any(k.startswith("gen_ai.usage") or k.startswith("llm.token_count") for k in a):
        return "chat"
    if "handoff.to" in a:
        return "handoff"
    return ""


def _messages_from(sp: dict, direction: str) -> list[dict]:
    a = sp["attrs"]
    v = _jl(a.get(f"gen_ai.{direction}.messages"))
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, list):
        return [m for m in (_norm_msg(x) for x in v) if m]
    if isinstance(v, str) and v:
        return [{"role": "user" if direction == "input" else "assistant", "content": v}]
    # OpenInference flattened: llm.input_messages.0.message.role / .content
    key = "llm.input_messages" if direction == "input" else "llm.output_messages"
    idx: dict[int, dict] = defaultdict(dict)
    for k, val in a.items():
        if k.startswith(key + "."):
            parts = k[len(key) + 1:].split(".")
            if parts[0].isdigit() and len(parts) >= 3:
                idx[int(parts[0])][parts[-1]] = val
    if idx:
        return [{"role": d.get("role", "user"), "content": d.get("content", "")} for _, d in sorted(idx.items())]
    # older GenAI conventions: events
    out = []
    for e in sp.get("events", []):
        n = e.get("name") or ""
        ea = e.get("attrs", {})
        if direction == "input" and n in ("gen_ai.system.message", "gen_ai.user.message", "gen_ai.assistant.message", "gen_ai.tool.message"):
            out.append({"role": n.split(".")[1], "content": _content(_jl(ea.get("content")))})
        if direction == "output" and n == "gen_ai.choice":
            msg = _jl(ea.get("message")) or {}
            out.append({"role": "assistant", "content": _content(msg.get("content") if isinstance(msg, dict) else msg)})
    if out:
        return out
    # legacy indexed attributes gen_ai.prompt.0.content / gen_ai.completion.0.content
    key2 = "gen_ai.prompt" if direction == "input" else "gen_ai.completion"
    idx2: dict[int, dict] = defaultdict(dict)
    for k, val in a.items():
        if k.startswith(key2 + "."):
            parts = k[len(key2) + 1:].split(".")
            if parts[0].isdigit() and len(parts) == 2:
                idx2[int(parts[0])][parts[1]] = val
    if idx2:
        return [{"role": d.get("role", "user" if direction == "input" else "assistant"), "content": d.get("content", "")} for _, d in sorted(idx2.items())]
    iv = a.get("input.value" if direction == "input" else "output.value")
    if iv:
        iv = _jl(iv)
        return [{"role": "user" if direction == "input" else "assistant", "content": _content(iv)}]
    return []


def _content(c: Any) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or (json.dumps(p) if p.get("type") not in ("tool_call", "function_call") else ""))
            else:
                parts.append(str(p))
        return "".join(x for x in parts if x)
    if isinstance(c, dict):
        for k in ("text", "content", "message", "prompt", "result", "output"):
            if isinstance(c.get(k), str):
                return c[k]
        return json.dumps(c, ensure_ascii=False)
    return str(c)


def _norm_msg(m: Any) -> dict | None:
    if not isinstance(m, dict):
        return {"role": "user", "content": str(m)} if m else None
    role = m.get("role") or "user"
    content = m.get("content")
    if content is None and "parts" in m:
        content = m["parts"]
    if role == "model":
        role = "assistant"
    if role not in ("system", "developer", "user", "assistant", "tool"):
        role = "user"
    return {"role": role, "content": _content(content)}


def _int(*vals) -> int:
    for v in vals:
        if v not in (None, ""):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                continue
    return 0


def _retrieval(sp: dict) -> list[ContextChunk]:
    a = sp["attrs"]
    docs = _jl(a.get("retrieval.documents") or a.get("gen_ai.retrieval.documents"))
    out = []
    if isinstance(docs, list):
        for i, d in enumerate(docs):
            if isinstance(d, dict):
                d = d.get("document", d)
                out.append(ContextChunk(id=str(d.get("id") or d.get("document.id") or f"doc-{i}"), text=_content(d.get("content") or d.get("text")),
                                        score=d.get("score")))
    idx: dict[int, dict] = defaultdict(dict)
    for k, v in a.items():
        if k.startswith("retrieval.documents.") and k.count(".") >= 4:
            parts = k.split(".")
            if parts[2].isdigit():
                idx[int(parts[2])][parts[-1]] = v
    for i, d in sorted(idx.items()):
        out.append(ContextChunk(id=str(d.get("id", f"doc-{i}")), text=str(d.get("content", "")), score=d.get("score")))
    return out


# ---------------------------------------------------------------------------
# grouping -> samples
# ---------------------------------------------------------------------------
def _session(sp: dict) -> str | None:
    a = {**sp.get("resource", {}), **sp["attrs"]}
    for k in ("session.id", "gen_ai.conversation.id", "aws.agentcore.session_id", "aws.bedrock.agentcore.session_id", "gcp.vertex.agent.session_id",
              "langfuse.session.id", "conversation.id"):
        if a.get(k):
            return str(a[k])
    return None


def spans_to_samples(raw: list[dict[str, Any]], origin: str = "source", group_by: str = "trace", target: str | None = None) -> list[Sample]:
    spans = []
    for r in raw:
        if "resourceSpans" in r:
            spans += [to_span(x) for x in expand_otlp(r)]
        else:
            spans.append(to_span(r))
    groups: dict[str, list[dict]] = defaultdict(list)
    for sp in spans:
        key = (_session(sp) if group_by == "session" else None) or sp.get("trace_id") or "unknown"
        groups[str(key)].append(sp)
    samples = []
    for key, grp in groups.items():
        samples.append(_build(key, grp, origin, target))
    return sorted(samples, key=lambda s: s.metadata.get("start_ns") or 0)


def _build(key: str, grp: list[dict], origin: str, target: str | None) -> Sample:
    grp = sorted(grp, key=lambda s: (s.get("start_ns") or 0))
    ids = {s["span_id"] for s in grp}
    roots = [s for s in grp if not s.get("parent_id") or s["parent_id"] not in ids] or grp[:1]
    root = roots[0]
    llm = [s for s in grp if _op(s) in LLM_OPS]
    tools = [s for s in grp if _op(s) in TOOL_OPS]
    s = Sample(case_id=_session(root) or root.get("trace_id") or key, target=target or (root.get("resource", {}).get("service.name") or origin),
               origin=origin, trace_id=root.get("trace_id"), session_id=next((x for x in (_session(sp) for sp in grp) if x), None))
    usage = Usage()
    for sp in llm:
        a = sp["attrs"]
        usage = usage.add(Usage(input_tokens=_int(a.get("gen_ai.usage.input_tokens"), a.get("gen_ai.usage.prompt_tokens"), a.get("llm.token_count.prompt")),
                                output_tokens=_int(a.get("gen_ai.usage.output_tokens"), a.get("gen_ai.usage.completion_tokens"), a.get("llm.token_count.completion")),
                                cached_tokens=_int(a.get("gen_ai.usage.cache_read_input_tokens"), a.get("gen_ai.usage.cached_tokens"))))
        s.model = s.model or a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or a.get("llm.model_name")
        s.provider = s.provider or a.get("gen_ai.system") or a.get("gen_ai.provider.name") or a.get("llm.provider")
    s.usage, s.steps = usage, len(llm)
    for sp in tools:
        a = sp["attrs"]
        name = a.get("gen_ai.tool.name") or a.get("tool.name") or (sp.get("name") or "tool").replace("execute_tool ", "")
        args = _jl(a.get("gen_ai.tool.call.arguments") or a.get("tool.parameters") or a.get("input.value") or {})
        res = _jl(a.get("gen_ai.tool.call.result") or a.get("output.value"))
        dur = ((sp["end_ns"] - sp["start_ns"]) / 1e6) if sp.get("end_ns") and sp.get("start_ns") else None
        s.trajectory.append(ToolCall(id=a.get("gen_ai.tool.call.id"), name=str(name), args=args if isinstance(args, dict) else {"input": args},
                                     result=res, latency_ms=dur, error=a.get("error.type")))
    for sp in grp:
        a = sp["attrs"]
        if a.get("handoff.to"):
            s.trajectory.append(ToolCall(name=f"handoff:{a['handoff.to']}", kind="handoff", args={"from": a.get("handoff.from"), "to": a["handoff.to"]}))
        if _op(sp) == "retrieve" or "retrieval.documents" in a or any(k.startswith("retrieval.documents.") for k in a):
            s.context += _retrieval(sp)
        for k, v in a.items():
            if "guardrail" in k and k.endswith("action") and v:
                s.metadata["guardrail"] = {"action": str(v).upper(), "attribute": k}
        if a.get("prompt.id") and a.get("prompt.version"):
            s.prompt_versions[str(a["prompt.id"])] = str(a["prompt.version"])
        for k in ("enduser.id", "user.id", "aws.agentcore.actor_id", "gen_ai.user.id"):
            if a.get(k):
                s.metadata["principal"] = str(a[k])
    msgs: list[dict] = []
    if llm:
        msgs = _messages_from(llm[-1], "input") + _messages_from(llm[-1], "output")
    if not msgs:
        msgs = _messages_from(root, "input") + _messages_from(root, "output")
    s.messages = [Message(role=m["role"], content=m["content"]) for m in msgs if m.get("content") is not None]
    users = [m for m in msgs if m["role"] == "user"]
    first_in = (_messages_from(llm[0], "input") if llm else []) or _messages_from(root, "input")
    first_user = next((m["content"] for m in first_in if m["role"] == "user"), None)
    s.input = first_user or (users[0]["content"] if users else None)
    outs = (_messages_from(root, "output") or (_messages_from(llm[-1], "output") if llm else []))
    s.output = next((m["content"] for m in reversed(outs) if m["role"] == "assistant" and m["content"]), None) or \
        next((m["content"] for m in reversed(msgs) if m["role"] == "assistant" and m["content"]), None)
    if root.get("start_ns") and grp:
        end = max((x.get("end_ns") or 0) for x in grp)
        s.latency_ms = (end - root["start_ns"]) / 1e6 if end else None
        s.metadata["start_ns"] = root["start_ns"]
        s.metadata["end_ns"] = end
    if llm and llm[0].get("start_ns") and llm[0].get("end_ns"):
        s.ttft_ms = (llm[0]["end_ns"] - (root.get("start_ns") or llm[0]["start_ns"])) / 1e6
    s.metadata["span_count"] = len(grp)
    s.metadata["content_captured"] = bool(s.output or s.input)
    return s


# ---------------------------------------------------------------------------
# joins: request/response logs and conversation history
# ---------------------------------------------------------------------------
def enrich(samples: list[Sample], records: list[dict[str, Any]], window_ms: float = 2000.0) -> int:
    """Fill missing prompts / outputs / usage from request-response records ({trace_id, session_id, request_id, time_ns,
    model, input_messages, output_text, usage}). Match on trace or session id, else on time inside the sample's span window."""
    filled = 0
    for s in samples:
        cands = [r for r in records if (r.get("trace_id") and r["trace_id"] == s.trace_id) or (r.get("session_id") and r["session_id"] == s.session_id)]
        if not cands and s.metadata.get("start_ns"):
            lo, hi = s.metadata["start_ns"] - window_ms * 1e6, (s.metadata.get("end_ns") or s.metadata["start_ns"]) + window_ms * 1e6
            cands = [r for r in records if r.get("time_ns") and lo <= r["time_ns"] <= hi and (not s.model or not r.get("model") or r["model"] in s.model or s.model in r["model"])]
        if not cands:
            continue
        cands.sort(key=lambda r: r.get("time_ns") or 0)
        first, last = cands[0], cands[-1]
        changed = False
        if not s.input:
            um = [m for m in first.get("input_messages", []) if m.get("role") == "user"]
            if um:
                s.input, changed = um[0]["content"], True
        if not s.output and last.get("output_text"):
            s.output, changed = last["output_text"], True
        if not s.messages and last.get("input_messages"):
            s.messages = [Message(role=m["role"], content=m["content"]) for m in last["input_messages"]] + \
                ([Message(role="assistant", content=last["output_text"])] if last.get("output_text") else [])
            changed = True
        if s.usage.total == 0:
            u = Usage()
            for r in cands:
                u = u.add(Usage(**{k: v for k, v in (r.get("usage") or {}).items() if k in Usage.model_fields}))
            if u.total:
                s.usage, changed = u, True
        s.model = s.model or last.get("model")
        if last.get("guardrail") and "guardrail" not in s.metadata:
            s.metadata["guardrail"] = last["guardrail"]
        if changed:
            s.metadata.setdefault("enriched_from", []).append(first.get("source", "logs"))
            filled += 1
    return filled


def attach_history(samples: list[Sample], history: dict[str, list[dict[str, Any]]]) -> int:
    """history: session_id -> [{role, content}] (AgentCore Memory events, Agent Engine Session events)."""
    n = 0
    for s in samples:
        h = history.get(s.session_id or "")
        if h:
            s.metadata["history"] = h
            if not s.messages:
                s.messages = [Message(role=m["role"], content=m["content"]) for m in h]
            if not s.input:
                s.input = next((m["content"] for m in h if m["role"] == "user"), None)
            if not s.output:
                s.output = next((m["content"] for m in reversed(h) if m["role"] == "assistant"), None)
            n += 1
    return n
