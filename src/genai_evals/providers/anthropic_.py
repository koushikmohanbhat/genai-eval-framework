"""Anthropic Messages API: direct, or Claude served by Google Vertex AI (`vertex_anthropic`)."""
from __future__ import annotations

import json
from typing import Any

from ..models import Message, ToolCall, Usage
from ..secrets import resolve_secret
from .base import Provider, ProviderResponse, split_system


class AnthropicProvider(Provider):
    @property
    def client(self):
        if self._client is None:
            import anthropic
            if self.profile.provider == "vertex_anthropic":
                self._client = anthropic.AnthropicVertex(project_id=self.profile.project, region=self.profile.location,
                                                         timeout=self.profile.timeout_s, max_retries=0)
            else:
                self._client = anthropic.Anthropic(api_key=resolve_secret(self.profile.auth, default_env="ANTHROPIC_API_KEY"),
                                                   timeout=self.profile.timeout_s, max_retries=0)
        return self._client

    @staticmethod
    def to_messages(messages: list[Message]) -> tuple[str | None, list[dict]]:
        system, rest = split_system(messages)
        out: list[dict] = []
        for m in rest:
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or ""}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args})
            out.append({"role": m.role, "content": blocks or [{"type": "text", "text": ""}]})
        return system, out

    def _complete(self, messages, tools, temperature, max_tokens, json_schema, json_mode):
        system, msgs = self.to_messages(messages)
        if json_schema or json_mode:
            hint = "Respond with a single JSON object only."
            if json_schema:
                hint += " It must follow this JSON schema: " + json.dumps(json_schema)
            system = f"{system}\n\n{hint}" if system else hint
        kw: dict[str, Any] = {"model": self.profile.model, "messages": msgs, "max_tokens": max_tokens or 2048}
        if system:
            kw["system"] = system
        if temperature is not None:
            kw["temperature"] = temperature
        if tools:
            kw["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                            "input_schema": t.get("parameters", {"type": "object", "properties": {}})} for t in tools]
        r = self.client.messages.create(**kw)
        return self.parse(r)

    @staticmethod
    def parse(r: Any) -> ProviderResponse:
        texts, calls = [], []
        for b in r.content:
            if b.type == "text":
                texts.append(b.text)
            elif b.type == "tool_use":
                calls.append(ToolCall(id=b.id, name=b.name, args=dict(b.input or {})))
        u = r.usage
        usage = Usage(input_tokens=(u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0),
                      output_tokens=u.output_tokens or 0, cached_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)
        return ProviderResponse(text="".join(texts), tool_calls=calls, usage=usage, model=getattr(r, "model", None),
                                metadata={"response_id": getattr(r, "id", None), "stop_reason": getattr(r, "stop_reason", None)})
