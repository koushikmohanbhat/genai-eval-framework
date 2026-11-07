"""Model / prompt, RAG, HTTP and Python-callable targets."""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from typing import Any

from ..models import Case, ContextChunk, Message, Sample, ToolCall, Usage
from .base import Target, import_ref, maybe_await, parse_json


class ModelTarget(Target):
    """A provider profile plus a prompt (versioned under prompts/ or inline)."""

    async def run(self, case: Case, repeat: int) -> Sample:
        s = self.new_sample(case, repeat)
        msgs, rp = self.build_messages(case)
        return await self._call(s, msgs, rp)

    async def _call(self, s: Sample, msgs: list[Message], rp) -> Sample:
        prov = self.ctx.pool.get(self.spec.profile)
        hints = rp.model_hints if rp else {}
        schema = rp.output_schema if rp else self.spec.options.get("output_schema")
        want_json = self.spec.output == "json" or bool(schema)
        t0 = time.perf_counter()
        r = await prov.complete(msgs, temperature=hints.get("temperature", self.spec.options.get("temperature")),
                                max_tokens=hints.get("max_tokens"), json_schema=schema, json_mode=want_json)
        s.latency_ms = (time.perf_counter() - t0) * 1000
        s.ttft_ms = r.ttft_ms
        s.output = r.text
        s.output_json = parse_json(r.text) if want_json else None
        s.usage, s.model, s.provider = r.usage, r.model, prov.profile.provider
        s.cost_usd = prov.cost(r.usage)
        s.messages = msgs + [Message(role="assistant", content=r.text)]
        s.steps = 1
        s.metadata.update({k: v for k, v in r.metadata.items() if v is not None})
        if rp:
            s.prompt_versions[rp.id] = rp.version
        return s


class RagTarget(ModelTarget):
    """Retriever + model. retriever: {type: python, callable: mod:fn} | {type: bedrock_kb, knowledge_base_id} | {type: vertex_rag, corpus}."""

    async def retrieve(self, query: str) -> list[ContextChunk]:
        r = self.spec.retriever or {}
        k = int(r.get("k", 5))
        typ = r.get("type", "python")
        if typ == "python":
            fn = import_ref(r["callable"], self.ctx.suite.base_dir)
            res = await maybe_await(fn(query, k))
            return [c if isinstance(c, ContextChunk) else ContextChunk(**c) for c in res][:k]
        if typ == "bedrock_kb":
            import boto3
            c = boto3.client("bedrock-agent-runtime", region_name=r.get("region"))
            resp = await asyncio.to_thread(c.retrieve, knowledgeBaseId=r["knowledge_base_id"], retrievalQuery={"text": query},
                                           retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": k}})
            out = []
            for i, it in enumerate(resp.get("retrievalResults", [])):
                meta = it.get("metadata", {}) or {}
                loc = it.get("location", {}) or {}
                uri = (loc.get("s3Location") or {}).get("uri") or (loc.get("webLocation") or {}).get("url")
                cid = meta.get(r.get("id_field", "x-amz-bedrock-kb-chunk-id")) or meta.get("id") or uri or f"chunk-{i}"
                out.append(ContextChunk(id=str(cid), text=it.get("content", {}).get("text", ""), score=it.get("score"), source=uri))
            return out
        if typ == "vertex_rag":
            from vertexai import rag  # type: ignore
            resp = await asyncio.to_thread(rag.retrieval_query, rag_resources=[rag.RagResource(rag_corpus=r["corpus"])], text=query,
                                           rag_retrieval_config=rag.RagRetrievalConfig(top_k=k))
            return [ContextChunk(id=str(getattr(c, "source_uri", i)), text=c.text, score=getattr(c, "score", None), source=getattr(c, "source_uri", None))
                    for i, c in enumerate(resp.contexts.contexts)]
        raise ValueError(f"unknown retriever type {typ}")

    async def run(self, case: Case, repeat: int) -> Sample:
        s = self.new_sample(case, repeat)
        t0 = time.perf_counter()
        chunks = await self.retrieve(case.input_text())
        s.metadata["retrieval_ms"] = (time.perf_counter() - t0) * 1000
        s.context = chunks
        s.trajectory.append(ToolCall(name="retrieve", args={"query": case.input_text()}, result=[c.id for c in chunks], kind="retrieval"))
        ctx_text = "\n\n".join(f"[{c.id}] {c.text}" for c in chunks)
        msgs, rp = self.build_messages(case, {"context": ctx_text, "question": case.input_text()})
        s = await self._call(s, msgs, rp)
        s.latency_ms = (s.latency_ms or 0) + s.metadata["retrieval_ms"]
        return s


def _path(obj: Any, dotted: str | None):
    if not dotted:
        return None
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class HttpTarget(Target):
    """POST to a deployed endpoint. options: url, body (Jinja-templated strings), headers_env {Header: ENV}, map {output, context, trajectory, ...}."""

    async def run(self, case: Case, repeat: int) -> Sample:
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment()
        o = self.spec.options
        vars_ = self.variables(case)

        def render(x):
            if isinstance(x, str):
                return env.from_string(x).render(**vars_, case_id=case.id)
            if isinstance(x, dict):
                return {k: render(v) for k, v in x.items()}
            if isinstance(x, list):
                return [render(v) for v in x]
            return x

        body = render(o.get("body", {"input": "{{ input }}"}))
        headers = {"Content-Type": "application/json", **{h: os.environ.get(e, "") for h, e in o.get("headers_env", {}).items()}}
        req = urllib.request.Request(o["url"], data=json.dumps(body).encode(), headers=headers, method="POST")
        s = self.new_sample(case, repeat)
        t0 = time.perf_counter()

        def _do():
            with urllib.request.urlopen(req, timeout=float(o.get("timeout_s", 120))) as r:  # noqa: S310 - operator-configured URL
                return json.loads(r.read() or b"{}")
        data = await asyncio.to_thread(_do)
        s.latency_ms = (time.perf_counter() - t0) * 1000
        return apply_mapping(s, data, o.get("map", {"output": "output"}))


def apply_mapping(s: Sample, data: Any, mapping: dict[str, str]) -> Sample:
    out = _path(data, mapping.get("output"))
    s.output = out if isinstance(out, str) or out is None else json.dumps(out)
    if isinstance(out, (dict, list)):
        s.output_json = out
    for c in _path(data, mapping.get("context")) or []:
        s.context.append(ContextChunk(**c) if isinstance(c, dict) and "id" in c else ContextChunk(id=str(c), text=""))
    for t in _path(data, mapping.get("trajectory")) or []:
        s.trajectory.append(ToolCall(**t) if isinstance(t, dict) else ToolCall(name=str(t)))
    if mapping.get("usage") and isinstance(_path(data, mapping["usage"]), dict):
        s.usage = Usage(**{k: v for k, v in _path(data, mapping["usage"]).items() if k in Usage.model_fields})
    for f in ("trace_id", "session_id", "cost_usd"):
        if mapping.get(f):
            setattr(s, f, _path(data, mapping[f]))
    if mapping.get("final_state"):
        s.final_state = _path(data, mapping["final_state"]) or {}
    return s


class PythonTarget(Target):
    """`callable: module:function`, called as fn(case, ctx) (sync or async). Returns str, dict of Sample fields, or a Sample."""

    async def run(self, case: Case, repeat: int) -> Sample:
        fn = import_ref(self.spec.callable, self.ctx.suite.base_dir)
        s = self.new_sample(case, repeat)
        t0 = time.perf_counter()
        res = await maybe_await(fn(case, {"repeat": repeat, "target": self.name, "options": self.spec.options, "run": self.ctx,
                                          "prompt": self.prompt}))
        elapsed = (time.perf_counter() - t0) * 1000
        if isinstance(res, Sample):
            res.case_id, res.target, res.repeat = case.id, self.name, repeat
            res.latency_ms = res.latency_ms if res.latency_ms is not None else elapsed
            return res
        if isinstance(res, str):
            s.output = res
        elif isinstance(res, dict):
            data = dict(res)
            if "output" in data and not isinstance(data["output"], str) and data["output"] is not None:
                data.setdefault("output_json", data["output"])
                data["output"] = json.dumps(data["output"])
            s = Sample(**{**s.model_dump(), **data})
        s.latency_ms = s.latency_ms if s.latency_ms is not None else elapsed
        if self.spec.output == "json" and s.output_json is None:
            s.output_json = parse_json(s.output)
        return s
