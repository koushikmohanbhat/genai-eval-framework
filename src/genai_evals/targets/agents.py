"""Agent targets: built-in tool loop (any provider), OpenAI Agents SDK, AgentCore Runtime, Bedrock Agents, Vertex Agent Engine."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from ..models import Case, ContextChunk, Message, Sample, ToolCall, Usage
from .base import Target, import_ref, maybe_await, parse_json
from .simulator import UserSimulator


def _user_turns(case: Case) -> list[str]:
    if case.turns:
        return list(case.turns)
    return [case.input_text()]


class ToolLoopAgentTarget(Target):
    """Provider-neutral agent loop: the model calls tools until it answers. Tool definitions come from the prompt's
    tools.yaml (or options.tools); implementations from `tools_module` (functions named after the tools, called as
    fn(args, state)). The module may define make_state(case) -> dict; that dict becomes Sample.final_state."""

    async def run(self, case: Case, repeat: int) -> Sample:
        s = self.new_sample(case, repeat)
        mod = import_ref(self.spec.tools_module, self.ctx.suite.base_dir) if self.spec.tools_module else None
        state: dict[str, Any] = (mod.make_state(case) if mod and hasattr(mod, "make_state") else {})
        msgs, rp = self.build_messages(case)
        tools = (rp.tools if rp and rp.tools else self.spec.options.get("tools", []))
        prov = self.ctx.pool.get(self.spec.profile)
        hints = rp.model_hints if rp else {}
        sim = UserSimulator.for_suite(self.ctx) if (case.goal and not case.turns) else None
        scripted = _user_turns(case)[1:] if case.turns else []
        if case.turns:
            msgs[-1] = Message(role="user", content=case.turns[0])
        usage, steps, t0 = Usage(), 0, time.perf_counter()
        max_turns = case.max_turns or (len(case.turns) if case.turns else 6)
        turn = 1
        while True:
            for _ in range(self.spec.max_steps):
                r = await prov.complete(msgs, tools, temperature=hints.get("temperature"))
                steps += 1
                usage = usage.add(r.usage)
                if s.ttft_ms is None:
                    s.ttft_ms = r.latency_ms
                msgs.append(Message(role="assistant", content=r.text or None, tool_calls=r.tool_calls))
                if not r.tool_calls:
                    break
                for tc in r.tool_calls:
                    tc.id = tc.id or f"call_{uuid.uuid4().hex[:8]}"
                    t1 = time.perf_counter()
                    fn = getattr(mod, tc.name, None) if mod else None
                    try:
                        if fn is None:
                            raise KeyError(f"unknown tool {tc.name}")
                        result = await maybe_await(fn(tc.args, state))
                    except Exception as e:  # noqa: BLE001 - tool errors are returned to the model, as in production
                        result, tc.error = {"error": str(e)}, str(e)
                    tc.result = result
                    tc.latency_ms = (time.perf_counter() - t1) * 1000
                    if isinstance(result, dict) and result.get("_handoff"):
                        tc.kind = "handoff"
                    s.trajectory.append(tc)
                    msgs.append(Message(role="tool", tool_call_id=tc.id, name=tc.name,
                                        content=result if isinstance(result, str) else json.dumps(result, default=str)))
            if turn >= max_turns:
                break
            nxt = scripted.pop(0) if scripted else (await sim.next(case, msgs, turn) if sim else None)
            if not nxt:
                break
            msgs.append(Message(role="user", content=nxt))
            turn += 1
        s.messages, s.steps, s.usage = msgs, steps, usage
        s.output = next((m.content for m in reversed(msgs) if m.role == "assistant" and m.content), "")
        s.output_json = parse_json(s.output) if self.spec.output == "json" else None
        s.final_state = state
        s.latency_ms = (time.perf_counter() - t0) * 1000
        s.cost_usd = prov.cost(usage)
        s.model, s.provider = prov.profile.model, prov.profile.provider
        s.metadata["turns"] = turn
        if rp:
            s.prompt_versions[rp.id] = rp.version
        return s


class OpenAIAgentsSDKTarget(Target):
    """`callable: module:build_agent` returns an `agents.Agent`. Runs it with `agents.Runner.run` and maps run items to a trajectory."""

    async def run(self, case: Case, repeat: int) -> Sample:
        from agents import Runner  # type: ignore
        build = import_ref(self.spec.callable, self.ctx.suite.base_dir)
        agent = await maybe_await(build(case, self.spec.options))
        s = self.new_sample(case, repeat)
        t0 = time.perf_counter()
        inp: Any = case.input if isinstance(case.input, list) else case.input_text()
        result = await Runner.run(agent, inp, max_turns=self.spec.max_steps)
        s.latency_ms = (time.perf_counter() - t0) * 1000
        pending: dict[str, ToolCall] = {}
        for item in getattr(result, "new_items", []) or []:
            typ = getattr(item, "type", "")
            raw = getattr(item, "raw_item", None)
            if typ == "tool_call_item":
                args = getattr(raw, "arguments", None) or (raw.get("arguments") if isinstance(raw, dict) else "{}")
                cid = getattr(raw, "call_id", None) or (raw.get("call_id") if isinstance(raw, dict) else None)
                tc = ToolCall(id=cid, name=getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else "tool"),
                              args=(parse_json(args) or {}) if isinstance(args, str) else (args or {}))
                pending[cid or tc.name] = tc
                s.trajectory.append(tc)
            elif typ == "tool_call_output_item":
                cid = raw.get("call_id") if isinstance(raw, dict) else getattr(raw, "call_id", None)
                if cid in pending:
                    pending[cid].result = getattr(item, "output", None)
            elif typ == "handoff_output_item":
                s.trajectory.append(ToolCall(name=f"handoff:{getattr(getattr(item, 'target_agent', None), 'name', 'agent')}", kind="handoff",
                                             args={"from": getattr(getattr(item, "source_agent", None), "name", None),
                                                   "to": getattr(getattr(item, "target_agent", None), "name", None)}))
        out = getattr(result, "final_output", "")
        s.output = out if isinstance(out, str) else json.dumps(out, default=lambda o: getattr(o, "model_dump", lambda: str(o))())
        s.output_json = None if isinstance(out, str) else parse_json(s.output)
        u = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if u:
            s.usage = Usage(input_tokens=getattr(u, "input_tokens", 0) or 0, output_tokens=getattr(u, "output_tokens", 0) or 0)
            s.steps = getattr(u, "requests", 0) or 0
        s.provider = "openai"
        s.model = getattr(agent, "model", None) if isinstance(getattr(agent, "model", None), str) else None
        return s


def _sse_join(text: str) -> str:
    if "data:" not in text:
        return text
    parts = []
    for line in text.splitlines():
        if line.startswith("data:"):
            d = line[5:].strip()
            try:
                v = json.loads(d)
                parts.append(v if isinstance(v, str) else (v.get("text") or v.get("delta") or json.dumps(v)))
            except json.JSONDecodeError:
                parts.append(d)
    return "".join(parts)


class AgentCoreRuntimeTarget(Target):
    """Amazon Bedrock AgentCore Runtime (InvokeAgentRuntime). options: agent_arn, region, qualifier, payload_key (prompt), output_key.
    The response carries no trajectory; set `enrich_from_spans: true` and run an `aws_agentcore_observability` source
    to attach the tool-call trajectory by session id (see docs/sources.md)."""

    async def run(self, case: Case, repeat: int) -> Sample:
        import boto3
        o = self.spec.options
        client = boto3.client("bedrock-agentcore", region_name=o.get("region"))
        session = f"genai-evals-{case.id}-{repeat}-{uuid.uuid4().hex}"[:100]
        payload = {o.get("payload_key", "prompt"): case.input_text(), **o.get("payload_extra", {})}
        kw = {"agentRuntimeArn": o["agent_arn"], "runtimeSessionId": session, "payload": json.dumps(payload).encode()}
        if o.get("qualifier"):
            kw["qualifier"] = o["qualifier"]
        s = self.new_sample(case, repeat)
        t0 = time.perf_counter()
        resp = await asyncio.to_thread(client.invoke_agent_runtime, **kw)
        body = resp.get("response")
        raw = body.read().decode() if hasattr(body, "read") else (b"".join(body).decode() if body is not None else "")
        s.latency_ms = (time.perf_counter() - t0) * 1000
        text = _sse_join(raw)
        data = parse_json(text)
        key = o.get("output_key", "result")
        s.output = (data.get(key) if isinstance(data, dict) and isinstance(data.get(key), str) else text)
        s.output_json = data if isinstance(data, dict) else None
        s.session_id = resp.get("runtimeSessionId", session)
        s.trace_id = resp.get("traceId")
        s.provider = "agentcore"
        return s


class BedrockAgentTarget(Target):
    """Amazon Bedrock Agents (InvokeAgent with enableTrace) -> output, tool calls, KB lookups, usage."""

    async def run(self, case: Case, repeat: int) -> Sample:
        import boto3
        o = self.spec.options
        client = boto3.client("bedrock-agent-runtime", region_name=o.get("region"))
        s = self.new_sample(case, repeat)
        s.session_id = f"ge-{case.id}-{repeat}-{uuid.uuid4().hex[:8]}"
        t0 = time.perf_counter()

        def _invoke():
            return client.invoke_agent(agentId=o["agent_id"], agentAliasId=o["agent_alias_id"], sessionId=s.session_id,
                                       inputText=case.input_text(), enableTrace=True)
        resp = await asyncio.to_thread(_invoke)
        chunks: list[str] = []
        usage = Usage()
        for ev in resp.get("completion", []):
            if "chunk" in ev:
                chunks.append(ev["chunk"].get("bytes", b"").decode())
            tr = ev.get("trace", {}).get("trace", {})
            orch = tr.get("orchestrationTrace", {})
            inv = orch.get("invocationInput", {})
            if inv.get("actionGroupInvocationInput"):
                ag = inv["actionGroupInvocationInput"]
                s.trajectory.append(ToolCall(name=ag.get("function") or ag.get("apiPath") or ag.get("actionGroupName", "action"),
                                             args={p["name"]: p.get("value") for p in ag.get("parameters", [])}))
            if inv.get("knowledgeBaseLookupInput"):
                kb = inv["knowledgeBaseLookupInput"]
                s.trajectory.append(ToolCall(name="knowledge_base", args={"query": kb.get("text"), "kb": kb.get("knowledgeBaseId")}, kind="retrieval"))
            obs = orch.get("observation", {})
            if obs.get("actionGroupInvocationOutput") and s.trajectory:
                s.trajectory[-1].result = obs["actionGroupInvocationOutput"].get("text")
            for i, ref in enumerate((obs.get("knowledgeBaseLookupOutput") or {}).get("retrievedReferences", [])):
                loc = (ref.get("location") or {}).get("s3Location", {}).get("uri")
                s.context.append(ContextChunk(id=ref.get("metadata", {}).get("x-amz-bedrock-kb-chunk-id") or loc or f"ref-{i}",
                                              text=(ref.get("content") or {}).get("text", ""), source=loc))
            mu = (orch.get("modelInvocationOutput") or {}).get("metadata", {}).get("usage")
            if mu:
                usage = usage.add(Usage(input_tokens=mu.get("inputTokens", 0), output_tokens=mu.get("outputTokens", 0)))
                s.steps += 1
            if tr.get("guardrailTrace"):
                s.metadata["guardrail"] = {"action": tr["guardrailTrace"].get("action"), "trace": tr["guardrailTrace"]}
        s.output, s.usage = "".join(chunks), usage
        s.latency_ms = (time.perf_counter() - t0) * 1000
        s.provider = "bedrock_agent"
        return s


class AgentEngineTarget(Target):
    """Vertex AI Agent Engine (ADK app): stream_query events -> output, function calls and responses."""

    async def run(self, case: Case, repeat: int) -> Sample:
        import vertexai  # type: ignore
        from vertexai import agent_engines  # type: ignore
        o = self.spec.options
        vertexai.init(project=o.get("project"), location=o.get("location"))
        app = agent_engines.get(o["resource"])
        s = self.new_sample(case, repeat)
        user_id = o.get("user_id", f"genai-evals-{case.id}")
        t0 = time.perf_counter()

        def _run():
            return list(app.stream_query(user_id=user_id, message=case.input_text()))
        events = await asyncio.to_thread(_run)
        texts: list[str] = []
        for ev in events:
            ev = ev if isinstance(ev, dict) else getattr(ev, "model_dump", lambda: {})()
            s.session_id = s.session_id or ev.get("session_id") or ev.get("invocation_id")
            for p in (ev.get("content") or {}).get("parts", []) or []:
                if p.get("text"):
                    texts.append(p["text"])
                if p.get("function_call"):
                    fc = p["function_call"]
                    s.trajectory.append(ToolCall(id=fc.get("id"), name=fc.get("name"), args=fc.get("args") or {}))
                if p.get("function_response"):
                    fr = p["function_response"]
                    for tc in reversed(s.trajectory):
                        if tc.name == fr.get("name") and tc.result is None:
                            tc.result = fr.get("response")
                            break
            um = ev.get("usage_metadata") or {}
            if um:
                s.usage = s.usage.add(Usage(input_tokens=um.get("prompt_token_count", 0) or 0, output_tokens=um.get("candidates_token_count", 0) or 0))
                s.steps += 1
        s.output = texts[-1] if texts else ""
        s.latency_ms = (time.perf_counter() - t0) * 1000
        s.provider = "agent_engine"
        return s
