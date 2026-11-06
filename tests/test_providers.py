import asyncio
from types import SimpleNamespace as NS

import pytest

from genai_evals.config import ProviderProfile, ProvidersConfig
from genai_evals.models import Message, ToolCall, Usage
from genai_evals.providers import build_provider

TOOLS = [{"name": "lookup_policy", "description": "d", "parameters": {"type": "object", "properties": {"policy_number": {"type": "string"}}}}]
CONVO = [Message(role="system", content="sys"), Message(role="user", content="hi"),
         Message(role="assistant", content=None, tool_calls=[ToolCall(id="c1", name="lookup_policy", args={"policy_number": "X"})]),
         Message(role="tool", tool_call_id="c1", name="lookup_policy", content='{"status": "active"}')]


def prof(**kw):
    return ProviderProfile(name="p", **kw)


class Rec:
    def __init__(self, ret):
        self.ret, self.kw = ret, None

    def __call__(self, **kw):
        self.kw = kw
        return self.ret


def test_openai_responses_request_and_parse():
    resp = {"id": "r1", "model": "m", "output": [{"type": "function_call", "call_id": "c2", "name": "lookup_policy", "arguments": '{"policy_number":"Y"}'},
                                                 {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 4}, "output_tokens_details": {"reasoning_tokens": 2}}}
    create = Rec(resp)
    client = NS(responses=NS(create=create))
    p = build_provider(prof(provider="openai", model="m", pricing={"input_per_mtok": 1, "output_per_mtok": 4, "cached_input_per_mtok": 0.5}), client)
    r = asyncio.run(p.complete(CONVO, TOOLS, temperature=0))
    kw = create.kw
    assert kw["store"] is False and kw["instructions"] == "sys" and kw["tools"][0]["type"] == "function"
    assert {"type": "function_call_output", "call_id": "c1", "output": '{"status": "active"}'} in kw["input"]
    assert r.text == "ok" and r.tool_calls[0].args == {"policy_number": "Y"}
    assert r.usage == Usage(input_tokens=10, output_tokens=5, cached_tokens=4, reasoning_tokens=2)
    assert p.cost(r.usage) == pytest.approx((6 * 1 + 4 * 0.5 + 5 * 4) / 1e6)


def test_anthropic_request_and_parse():
    resp = NS(content=[NS(type="text", text="hello"), NS(type="tool_use", id="t1", name="lookup_policy", input={"policy_number": "Z"})],
              usage=NS(input_tokens=7, output_tokens=3, cache_read_input_tokens=2), model="claude", id="m1", stop_reason="tool_use")
    create = Rec(resp)
    p = build_provider(prof(provider="anthropic", model="claude"), NS(messages=NS(create=create)))
    r = asyncio.run(p.complete(CONVO, TOOLS, json_mode=True))
    assert create.kw["system"].startswith("sys") and "JSON" in create.kw["system"]
    assert create.kw["messages"][-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"status": "active"}'}]}
    assert create.kw["tools"][0]["input_schema"]["type"] == "object"
    assert r.text == "hello" and r.tool_calls[0].name == "lookup_policy" and r.usage.input_tokens == 9


def test_bedrock_converse_request_and_parse():
    resp = {"output": {"message": {"content": [{"text": "done"}, {"toolUse": {"toolUseId": "u1", "name": "lookup_policy", "input": {"policy_number": "Q"}}}]}},
            "usage": {"inputTokens": 11, "outputTokens": 6}, "metrics": {"latencyMs": 321}, "stopReason": "tool_use",
            "trace": {"guardrail": {"x": 1}}}
    conv = Rec(resp)
    p = build_provider(prof(provider="bedrock", model="anthropic.claude-x", region="ap-south-1", options={"guardrail_id": "g1"}), NS(converse=conv))
    r = asyncio.run(p.complete(CONVO, TOOLS, temperature=0.1, max_tokens=99))
    kw = conv.kw
    assert kw["system"] == [{"text": "sys"}] and kw["inferenceConfig"] == {"temperature": 0.1, "maxTokens": 99}
    assert kw["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["type"] == "object"
    assert kw["guardrailConfig"]["guardrailIdentifier"] == "g1"
    assert kw["messages"][-1]["content"][0]["toolResult"]["toolUseId"] == "c1"
    assert r.text == "done" and r.tool_calls[0].args == {"policy_number": "Q"} and r.latency_ms == 321 and r.metadata["guardrail"]["action"] == "NONE"
    assert p.profile.model_family == "anthropic"


def test_vertex_request_and_parse():
    resp = {"candidates": [{"content": {"parts": [{"text": "gem"}, {"function_call": {"name": "lookup_policy", "args": {"policy_number": "G"}}}]},
                            "safety_ratings": [{"category": "HARM", "probability": "LOW", "blocked": False}], "finish_reason": "STOP"}],
            "usage_metadata": {"prompt_token_count": 12, "candidates_token_count": 4, "thoughts_token_count": 3}}
    gen = Rec(resp)
    p = build_provider(prof(provider="vertex", model="gemini", project="p", location="asia-south1"), NS(models=NS(generate_content=gen)))
    r = asyncio.run(p.complete(CONVO, TOOLS, json_schema={"type": "object"}))
    kw = gen.kw
    assert kw["config"]["system_instruction"] == "sys" and kw["config"]["response_mime_type"] == "application/json"
    assert kw["contents"][1]["role"] == "model" and kw["contents"][2]["parts"][0]["function_response"]["name"] == "lookup_policy"
    assert r.text == "gem" and r.tool_calls[0].args == {"policy_number": "G"} and r.usage.reasoning_tokens == 3


def test_openai_compatible_chat_completions():
    msg = NS(content="hey", tool_calls=[NS(id="k", function=NS(name="lookup_policy", arguments='{"policy_number":"W"}'))])
    create = Rec(NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=3, completion_tokens=2), model="llama"))
    p = build_provider(prof(provider="openai_compatible", model="llama", base_url="http://gw/v1"), NS(chat=NS(completions=NS(create=create))))
    r = asyncio.run(p.complete(CONVO, TOOLS))
    assert create.kw["messages"][2]["tool_calls"][0]["function"]["name"] == "lookup_policy" and create.kw["messages"][3]["tool_call_id"] == "c1"
    assert r.tool_calls[0].args == {"policy_number": "W"} and r.usage.total == 5


def test_retries_transient_errors_only():
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 Service Unavailable")
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}}
    p = build_provider(prof(provider="bedrock", model="m", retries=3), NS(converse=flaky))
    p.retry_base_s = 0
    assert asyncio.run(p.complete([Message(role="user", content="x")])).text == "ok" and calls["n"] == 3

    def bad(**kw):
        raise ValueError("validation")
    p2 = build_provider(prof(provider="bedrock", model="m", retries=3), NS(converse=bad))
    with pytest.raises(ValueError):
        asyncio.run(p2.complete([Message(role="user", content="x")]))


def test_profiles_reject_inline_secrets_and_unknown_providers():
    with pytest.raises(ValueError, match="secrets"):
        ProvidersConfig(profiles={"x": {"provider": "openai", "model": "m", "auth": {"api_key": "sk-123"}}})
    with pytest.raises(ValueError, match="unknown provider"):
        ProvidersConfig(profiles={"x": {"provider": "azure-magic", "model": "m"}})


def test_mock_judge_and_embeddings():
    p = build_provider(prof(provider="mock", model="j", options={"mode": "judge"}))
    out = asyncio.run(p.complete([Message(role="user", content="<task>reference_match</task><reference>within 48 hours</reference><output>Tell us within 48 hours.</output>")]))
    assert '"pass": true' in out.text
    e = asyncio.run(p.embed(["claim within 48 hours", "claim within 48 hours", "unrelated words here"]))
    assert e[0] == e[1] and e[0] != e[2]
