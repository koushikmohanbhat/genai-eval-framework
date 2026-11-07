"""OpenAI direct (Responses API) and any OpenAI-compatible endpoint (Chat Completions)."""
from __future__ import annotations

import json
from typing import Any

from ..models import Message, ToolCall, Usage
from ..secrets import resolve_secret
from .base import Provider, ProviderResponse, split_system


def _obj(x: Any, k: str, default=None):
    return x.get(k, default) if isinstance(x, dict) else getattr(x, k, default)


class OpenAIResponsesProvider(Provider):
    """Responses API. `store` defaults to False so prompts and outputs are not retained as stored responses."""

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=resolve_secret(self.profile.auth, default_env="OPENAI_API_KEY"),
                                  base_url=self.profile.base_url, timeout=self.profile.timeout_s, max_retries=0)
        return self._client

    @staticmethod
    def to_input(messages: list[Message]) -> tuple[str | None, list[dict]]:
        instructions, rest = split_system(messages)
        items: list[dict] = []
        for m in rest:
            if m.role == "tool":
                items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.content or ""})
                continue
            if m.content:
                items.append({"role": m.role, "content": m.content})
            for tc in m.tool_calls:
                items.append({"type": "function_call", "call_id": tc.id, "name": tc.name, "arguments": json.dumps(tc.args)})
        return instructions, items

    @staticmethod
    def to_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                 "parameters": t.get("parameters", {"type": "object", "properties": {}})} for t in tools]

    def _complete(self, messages, tools, temperature, max_tokens, json_schema, json_mode):
        instructions, items = self.to_input(messages)
        opts = {k: v for k, v in self.profile.options.items() if k not in {"temperature", "max_tokens"}}
        kw: dict[str, Any] = {"model": self.profile.model, "input": items, "store": opts.pop("store", False), **opts}
        if instructions:
            kw["instructions"] = instructions
        if tools:
            kw["tools"] = self.to_tools(tools)
        if temperature is not None:
            kw["temperature"] = temperature
        if max_tokens:
            kw["max_output_tokens"] = max_tokens
        if json_schema:
            kw["text"] = {"format": {"type": "json_schema", "name": "output", "schema": json_schema, "strict": False}}
        elif json_mode:
            kw["text"] = {"format": {"type": "json_object"}}
        r = self.client.responses.create(**kw)
        return self.parse(r)

    @staticmethod
    def parse(r: Any) -> ProviderResponse:
        texts, calls = [], []
        for item in _obj(r, "output", []) or []:
            t = _obj(item, "type")
            if t == "message":
                for c in _obj(item, "content", []) or []:
                    if _obj(c, "type") in ("output_text", "text"):
                        texts.append(_obj(c, "text", ""))
            elif t == "function_call":
                args = _obj(item, "arguments", "{}")
                try:
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                except json.JSONDecodeError:
                    args = {"_raw": args}
                calls.append(ToolCall(id=_obj(item, "call_id"), name=_obj(item, "name"), args=args))
        u = _obj(r, "usage")
        usage = Usage()
        if u:
            usage = Usage(input_tokens=_obj(u, "input_tokens", 0) or 0, output_tokens=_obj(u, "output_tokens", 0) or 0,
                          cached_tokens=_obj(_obj(u, "input_tokens_details", {}) or {}, "cached_tokens", 0) or 0,
                          reasoning_tokens=_obj(_obj(u, "output_tokens_details", {}) or {}, "reasoning_tokens", 0) or 0)
        return ProviderResponse(text="".join(texts) or (_obj(r, "output_text", "") or ""), tool_calls=calls, usage=usage,
                                model=_obj(r, "model"), raw=None, metadata={"response_id": _obj(r, "id")})

    def _embed(self, texts):
        model = self.profile.options.get("embedding_model", self.profile.model)
        r = self.client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in r.data]


class OpenAICompatibleProvider(Provider):
    """Chat Completions against LiteLLM, an AI gateway, vLLM or any OpenAI-compatible server."""

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=resolve_secret(self.profile.auth, default_env="OPENAI_API_KEY") or "unused",
                                  base_url=self.profile.base_url, timeout=self.profile.timeout_s, max_retries=0)
        return self._client

    @staticmethod
    def to_messages(messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            role = "system" if m.role == "developer" else m.role
            d: dict[str, Any] = {"role": role, "content": m.content}
            if m.tool_calls:
                d["tool_calls"] = [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                                   for tc in m.tool_calls]
            if m.role == "tool":
                d["tool_call_id"] = m.tool_call_id
            out.append(d)
        return out

    def _complete(self, messages, tools, temperature, max_tokens, json_schema, json_mode):
        kw: dict[str, Any] = {"model": self.profile.model, "messages": self.to_messages(messages)}
        if tools:
            kw["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                             "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
                           for t in tools]
        if temperature is not None:
            kw["temperature"] = temperature
        if max_tokens:
            kw["max_tokens"] = max_tokens
        if json_schema:
            kw["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": json_schema}}
        elif json_mode:
            kw["response_format"] = {"type": "json_object"}
        r = self.client.chat.completions.create(**kw)
        msg = r.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        u = r.usage
        usage = Usage(input_tokens=getattr(u, "prompt_tokens", 0) or 0, output_tokens=getattr(u, "completion_tokens", 0) or 0) if u else Usage()
        return ProviderResponse(text=msg.content or "", tool_calls=calls, usage=usage, model=getattr(r, "model", None))

    def _embed(self, texts):
        r = self.client.embeddings.create(model=self.profile.options.get("embedding_model", self.profile.model), input=texts)
        return [d.embedding for d in r.data]
