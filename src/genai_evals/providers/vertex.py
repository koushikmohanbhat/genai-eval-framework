"""Google Vertex AI Gemini through the google-genai SDK (vertexai=True)."""
from __future__ import annotations

import uuid
from typing import Any

from ..models import Message, ToolCall, Usage
from .base import Provider, ProviderResponse, split_system


def _g(x: Any, k: str, default=None):
    return x.get(k, default) if isinstance(x, dict) else getattr(x, k, default)


class VertexGeminiProvider(Provider):
    @property
    def client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(vertexai=True, project=self.profile.project, location=self.profile.location or "global")
        return self._client

    @staticmethod
    def to_contents(messages: list[Message]) -> tuple[str | None, list[dict]]:
        system, rest = split_system(messages)
        names: dict[str, str] = {}
        out: list[dict] = []
        for m in rest:
            if m.role == "tool":
                part = {"function_response": {"name": names.get(m.tool_call_id or "", m.name or "tool"), "response": {"result": m.content}}}
                if out and out[-1]["role"] == "user" and all("function_response" in p for p in out[-1]["parts"]):
                    out[-1]["parts"].append(part)
                else:
                    out.append({"role": "user", "parts": [part]})
                continue
            parts: list[dict] = []
            if m.content:
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                names[tc.id or ""] = tc.name
                parts.append({"function_call": {"name": tc.name, "args": tc.args}})
            out.append({"role": "model" if m.role == "assistant" else "user", "parts": parts or [{"text": " "}]})
        return system, out

    def _complete(self, messages, tools, temperature, max_tokens, json_schema, json_mode):
        system, contents = self.to_contents(messages)
        cfg: dict[str, Any] = {}
        if system:
            cfg["system_instruction"] = system
        if temperature is not None:
            cfg["temperature"] = temperature
        if max_tokens:
            cfg["max_output_tokens"] = max_tokens
        if tools:
            cfg["tools"] = [{"function_declarations": [{"name": t["name"], "description": t.get("description", ""),
                                                        "parameters": t.get("parameters", {"type": "object", "properties": {}})} for t in tools]}]
        if json_schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = json_schema
        elif json_mode:
            cfg["response_mime_type"] = "application/json"
        for k, v in self.profile.options.items():
            if k not in {"temperature", "max_tokens", "embedding_model"}:
                cfg[k] = v
        r = self.client.models.generate_content(model=self.profile.model, contents=contents, config=cfg)
        return self.parse(r)

    @staticmethod
    def parse(r: Any) -> ProviderResponse:
        texts, calls, safety = [], [], []
        finish = ""
        for cand in (_g(r, "candidates") or [])[:1]:
            content = _g(cand, "content")
            for p in (_g(content, "parts") or []) if content else []:
                if _g(p, "text"):
                    texts.append(_g(p, "text"))
                fc = _g(p, "function_call")
                if fc:
                    calls.append(ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=_g(fc, "name"), args=dict(_g(fc, "args") or {})))
            for s in _g(cand, "safety_ratings") or []:
                safety.append({"category": str(_g(s, "category")), "probability": str(_g(s, "probability")), "blocked": bool(_g(s, "blocked"))})
            finish = str(_g(cand, "finish_reason") or "")
        um = _g(r, "usage_metadata")
        usage = Usage()
        if um:
            usage = Usage(input_tokens=_g(um, "prompt_token_count", 0) or 0, output_tokens=_g(um, "candidates_token_count", 0) or 0,
                          cached_tokens=_g(um, "cached_content_token_count", 0) or 0, reasoning_tokens=_g(um, "thoughts_token_count", 0) or 0)
        meta: dict[str, Any] = {"response_id": _g(r, "response_id")}
        if safety:
            meta["safety_ratings"] = safety
            if any(s["blocked"] for s in safety) or "SAFETY" in finish:
                meta["guardrail"] = {"action": "INTERVENED", "source": "gemini_safety"}
        return ProviderResponse(text="".join(texts), tool_calls=calls, usage=usage, model=_g(r, "model_version"), metadata=meta)

    def _embed(self, texts):
        model = self.profile.options.get("embedding_model", "text-embedding-005")
        r = self.client.models.embed_content(model=model, contents=texts)
        return [list(e.values) for e in r.embeddings]

