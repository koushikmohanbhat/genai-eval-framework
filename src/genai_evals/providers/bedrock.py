"""Amazon Bedrock Converse API (any Bedrock model, including Claude, Llama, Nova and OpenAI open-weight models)."""
from __future__ import annotations

import json
from typing import Any

from ..models import Message, ToolCall, Usage
from .base import Provider, ProviderResponse, split_system


class BedrockConverseProvider(Provider):
    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            session = boto3.Session(profile_name=self.profile.auth.get("aws_profile")) if self.profile.auth.get("aws_profile") else boto3.Session()
            if self.profile.auth.get("role_arn"):
                creds = session.client("sts").assume_role(RoleArn=self.profile.auth["role_arn"], RoleSessionName="genai-evals")["Credentials"]
                session = boto3.Session(aws_access_key_id=creds["AccessKeyId"], aws_secret_access_key=creds["SecretAccessKey"],
                                        aws_session_token=creds["SessionToken"])
            self._client = session.client("bedrock-runtime", region_name=self.profile.region,
                                          config=Config(read_timeout=self.profile.timeout_s, retries={"max_attempts": 1}))
        return self._client

    @staticmethod
    def to_messages(messages: list[Message]) -> tuple[list[dict], list[dict]]:
        system, rest = split_system(messages)
        out: list[dict] = []
        for m in rest:
            if m.role == "tool":
                block = {"toolResult": {"toolUseId": m.tool_call_id, "content": [{"text": m.content or ""}]}}
                if out and out[-1]["role"] == "user" and any("toolResult" in b for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            blocks: list[dict] = []
            if m.content:
                blocks.append({"text": m.content})
            for tc in m.tool_calls:
                blocks.append({"toolUse": {"toolUseId": tc.id, "name": tc.name, "input": tc.args}})
            out.append({"role": m.role, "content": blocks or [{"text": " "}]})
        return ([{"text": system}] if system else []), out

    def _complete(self, messages, tools, temperature, max_tokens, json_schema, json_mode):
        system, msgs = self.to_messages(messages)
        if json_schema or json_mode:
            hint = "Respond with a single JSON object only." + (" Schema: " + json.dumps(json_schema) if json_schema else "")
            system = system + [{"text": hint}]
        kw: dict[str, Any] = {"modelId": self.profile.model, "messages": msgs}
        if system:
            kw["system"] = system
        inf = {}
        if temperature is not None:
            inf["temperature"] = temperature
        if max_tokens:
            inf["maxTokens"] = max_tokens
        if inf:
            kw["inferenceConfig"] = inf
        if tools:
            kw["toolConfig"] = {"tools": [{"toolSpec": {"name": t["name"], "description": t.get("description", "") or t["name"],
                                                        "inputSchema": {"json": t.get("parameters", {"type": "object", "properties": {}})}}}
                                          for t in tools]}
        opts = self.profile.options
        if opts.get("guardrail_id"):
            kw["guardrailConfig"] = {"guardrailIdentifier": opts["guardrail_id"], "guardrailVersion": str(opts.get("guardrail_version", "DRAFT")),
                                     "trace": "enabled"}
        if opts.get("additional_model_request_fields"):
            kw["additionalModelRequestFields"] = opts["additional_model_request_fields"]
        r = self.client.converse(**kw)
        return self.parse(r)

    @staticmethod
    def parse(r: dict) -> ProviderResponse:
        texts, calls = [], []
        for b in r.get("output", {}).get("message", {}).get("content", []):
            if "text" in b:
                texts.append(b["text"])
            elif "toolUse" in b:
                tu = b["toolUse"]
                calls.append(ToolCall(id=tu.get("toolUseId"), name=tu["name"], args=tu.get("input") or {}))
            elif "reasoningContent" in b:
                continue
        u = r.get("usage", {})
        usage = Usage(input_tokens=u.get("inputTokens", 0), output_tokens=u.get("outputTokens", 0),
                      cached_tokens=u.get("cacheReadInputTokens", 0) or 0)
        meta = {"stop_reason": r.get("stopReason"), "request_id": r.get("ResponseMetadata", {}).get("RequestId")}
        if r.get("trace", {}).get("guardrail"):
            meta["guardrail"] = {"action": "INTERVENED" if r.get("stopReason") == "guardrail_intervened" else "NONE",
                                 "trace": r["trace"]["guardrail"]}
        return ProviderResponse(text="".join(texts), tool_calls=calls, usage=usage,
                                latency_ms=float(r.get("metrics", {}).get("latencyMs", 0) or 0), metadata=meta)

    def _embed(self, texts):
        model = self.profile.options.get("embedding_model", "amazon.titan-embed-text-v2:0")
        out = []
        for t in texts:
            r = self.client.invoke_model(modelId=model, body=json.dumps({"inputText": t}))
            out.append(json.loads(r["body"].read())["embedding"])
        return out
