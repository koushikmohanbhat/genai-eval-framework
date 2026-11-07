"""Target adapters: the *system* under test (model, prompt, RAG pipeline, agent, endpoint)."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ProvidersConfig, SuiteSpec, TargetSpec
from ..models import Case, Message, Sample
from ..prompts import PromptStore, RenderedPrompt
from ..providers import ProviderPool


@dataclass
class RunContext:
    suite: SuiteSpec
    providers: ProvidersConfig
    pool: ProviderPool
    prompts: PromptStore | None = None
    prompt_overrides: dict[str, str] = field(default_factory=dict)   # {"claims_agent": "1.4.0"}
    extra: dict[str, Any] = field(default_factory=dict)


def import_ref(ref: str, base_dir: str | None = None):
    """'package.module:attr' -> object. The suite folder comes first on sys.path, so a project's own modules
    (sim_system.py, tools.py, ...) win. Several projects in one process (tests, pack validation) can have modules
    with the same name: a cached module loaded from another folder is dropped so this folder's file is imported."""
    mod, _, attr = ref.partition(":")
    if base_dir:
        bd = str(Path(base_dir).resolve())
        if bd in sys.path:
            sys.path.remove(bd)
        sys.path.insert(0, bd)
        for f in Path(bd).glob("*.py"):
            cached = sys.modules.get(f.stem)
            cf = getattr(cached, "__file__", None)
            if cached is not None and cf and Path(cf).resolve() != f.resolve():
                del sys.modules[f.stem]
    m = importlib.import_module(mod)
    return getattr(m, attr) if attr else m


async def maybe_await(x):
    return await x if inspect.isawaitable(x) else x


def parse_json(text: str | None) -> Any:
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except json.JSONDecodeError:
                return None
    return None


class Target:
    def __init__(self, spec: TargetSpec, ctx: RunContext):
        self.spec = spec
        self.ctx = ctx

    @property
    def name(self) -> str:
        return self.spec.name

    def config_fingerprint(self) -> dict[str, Any]:
        fp = self.spec.model_dump()
        if self.spec.profile and self.spec.profile in self.ctx.providers.profiles:
            p = self.ctx.providers.get(self.spec.profile)
            fp["_profile"] = {"provider": p.provider, "model": p.model, "options": p.options}
        rp = self.prompt()
        if rp:
            fp["_prompt"] = rp.ref + " " + rp.hash
        return fp

    def provider_family(self) -> str | None:
        if self.spec.profile and self.spec.profile in self.ctx.providers.profiles:
            return self.ctx.providers.get(self.spec.profile).model_family
        return self.spec.options.get("family")

    # ---- prompt resolution ---------------------------------------------
    def prompt(self, variables: dict[str, Any] | None = None) -> RenderedPrompt | None:
        if not self.spec.prompt:
            return None
        if self.ctx.prompts is None:
            raise RuntimeError(f"target '{self.name}' references prompt '{self.spec.prompt}' but no prompts_dir is configured")
        pid, _, ver = self.spec.prompt.partition("@")
        ver = self.ctx.prompt_overrides.get(pid, ver)
        ref = f"{pid}@{ver}" if ver else pid
        return self.ctx.prompts.render(ref, self.spec.prompt_stage, variables or {})

    def variables(self, case: Case) -> dict[str, Any]:
        v: dict[str, Any] = dict(case.input) if isinstance(case.input, dict) else {}
        v.setdefault("input", case.input_text())
        v.update(self.spec.options.get("variables", {}))
        return v

    def build_messages(self, case: Case, extra_vars: dict[str, Any] | None = None) -> tuple[list[Message], RenderedPrompt | None]:
        vars_ = {**self.variables(case), **(extra_vars or {})}
        rp = self.prompt(vars_)
        msgs: list[Message] = []
        system = rp.system if rp else self.spec.system
        if system:
            from jinja2.sandbox import SandboxedEnvironment
            if not rp and "{{" in system:
                system = SandboxedEnvironment().from_string(system).render(**vars_)
            msgs.append(Message(role="system", content=system))
        for ex in (rp.examples if rp else []):
            msgs.append(Message(role="user", content=str(ex.get("input", ""))))
            out = ex.get("output", "")
            msgs.append(Message(role="assistant", content=out if isinstance(out, str) else json.dumps(out)))
        if isinstance(case.input, list):
            msgs += [Message(**m) for m in case.input]
        else:
            if rp and rp.template:
                user = rp.render_user(vars_)
            elif self.spec.template:
                from jinja2.sandbox import SandboxedEnvironment
                user = SandboxedEnvironment().from_string(self.spec.template).render(**vars_)
            else:
                user = case.input_text()
            msgs.append(Message(role="user", content=user))
        return msgs, rp

    def new_sample(self, case: Case, repeat: int) -> Sample:
        return Sample(case_id=case.id, target=self.name, repeat=repeat, input=case.input)

    async def run(self, case: Case, repeat: int) -> Sample:  # pragma: no cover
        raise NotImplementedError


def build_target(spec: TargetSpec, ctx: RunContext) -> Target:
    from .agents import AgentCoreRuntimeTarget, AgentEngineTarget, BedrockAgentTarget, OpenAIAgentsSDKTarget, ToolLoopAgentTarget
    from .basic import HttpTarget, ModelTarget, PythonTarget, RagTarget
    if spec.kind in ("model", "prompt"):
        return ModelTarget(spec, ctx)
    if spec.kind == "rag":
        return RagTarget(spec, ctx)
    if spec.kind == "http":
        return HttpTarget(spec, ctx)
    if spec.kind == "python":
        return PythonTarget(spec, ctx)
    fw = spec.framework or "tool_loop"
    builtin = {"tool_loop": ToolLoopAgentTarget, "openai_agents_sdk": OpenAIAgentsSDKTarget, "agentcore_runtime": AgentCoreRuntimeTarget,
               "bedrock_agent": BedrockAgentTarget, "agent_engine": AgentEngineTarget}
    if fw in builtin:
        return builtin[fw](spec, ctx)
    from ..plugins import load
    return load("target", fw)(spec, ctx)                       # agent framework adapter from a plugin


__all__ = ["RunContext", "Target", "build_target", "import_ref", "maybe_await", "parse_json", "Path", "asyncio"]
