"""Core data model: cases, samples, scores.

A *Sample* is the one shape every target adapter and every telemetry source produces,
so the same graders work offline (fresh runs) and online (production traffic).
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """Provider-neutral chat message (OpenAI-like)."""
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    latency_ms: float | None = None
    kind: Literal["tool", "handoff", "retrieval", "guardrail"] = "tool"


class ContextChunk(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    text: str = ""
    score: float | None = None
    source: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: Usage) -> Usage:
        return Usage(input_tokens=self.input_tokens + other.input_tokens,
                     output_tokens=self.output_tokens + other.output_tokens,
                     cached_tokens=self.cached_tokens + other.cached_tokens,
                     reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens)


class Case(BaseModel):
    """One test case in a golden set."""
    model_config = ConfigDict(extra="allow")
    id: str
    input: Any                                   # str, list of messages, or dict of template variables
    reference: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    critical: bool = False
    source: str = "sme"                          # synthetic | production | sme
    lineage_ref: str | None = None               # DPDP framework artifact id for this row
    turns: list[str] | None = None               # scripted user turns for multi-turn cases
    goal: str | None = None                      # goal card for the user simulator
    persona: str | None = None
    max_turns: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def input_text(self) -> str:
        if isinstance(self.input, str):
            return self.input
        if isinstance(self.input, dict):
            for k in ("text", "question", "query", "message", "input"):
                if isinstance(self.input.get(k), str):
                    return self.input[k]
            return json.dumps(self.input, ensure_ascii=False)
        if isinstance(self.input, list):
            users = [m.get("content", "") for m in self.input if isinstance(m, dict) and m.get("role") == "user"]
            return users[-1] if users else json.dumps(self.input, ensure_ascii=False)
        return str(self.input)


class Sample(BaseModel):
    """What a system did for one case (or one production session)."""
    model_config = ConfigDict(extra="allow")
    case_id: str
    target: str
    repeat: int = 0
    origin: str = "offline"                      # offline | source:<name> | import:<tool>
    input: Any = None
    output: str | None = None
    output_json: Any = None
    messages: list[Message] = Field(default_factory=list)
    context: list[ContextChunk] = Field(default_factory=list)
    trajectory: list[ToolCall] = Field(default_factory=list)
    final_state: dict[str, Any] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    steps: int = 0
    model: str | None = None
    provider: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def tool_calls(self, kind: str | None = "tool") -> list[ToolCall]:
        return [t for t in self.trajectory if kind is None or t.kind == kind]


class Score(BaseModel):
    model_config = ConfigDict(extra="allow")
    grader: str
    case_id: str
    target: str
    repeat: int = 0
    value: float | None = None                  # normalised 0..1 (None when skipped)
    passed: bool | None = None
    label: str | None = None
    reasoning: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = 0.0
    skipped: bool = False
    skip_reason: str | None = None
    error: str | None = None


Message.model_rebuild()
