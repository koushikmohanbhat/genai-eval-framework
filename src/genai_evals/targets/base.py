"""Target adapters: the *system* under test (model, prompt, RAG pipeline, agent, endpoint)."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import re
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ProvidersConfig, SuiteSpec, TargetSpec
from ..models import Case, Message, Sample
from ..prompts import PromptStore, RenderedPrompt
from ..providers import ProviderPool

#: the current trial (one case x repeat on one target): {"trial_id": ..., plus anything the reset hook returned}.
#: HTTP bodies can use {{ trial_id }} / {{ trial }}; Python targets get it as ctx["trial"].
CURRENT_TRIAL: ContextVar[dict[str, Any] | None] = ContextVar("genai_evals_trial", default=None)


def current_trial() -> dict[str, Any]:
    return CURRENT_TRIAL.get() or {}


async def reset_trial(spec: TargetSpec, case: Case, repeat: int, trial: dict[str, Any], base_dir: str | None) -> dict[str, Any]:
    """Run the target's per-trial reset hook, so every trial starts from a clean environment.

    reset: {python: "module:function"}  -> function(case, repeat, trial), sync or async; may return a dict merged into
                                           the trial (e.g. {"sandbox_id": ...}) for the target to use
    reset: {url: ..., body: {...}, headers_env: {...}, timeout_s: 30}  -> POST (Jinja-templated with case_id, repeat,
                                           trial_id); a JSON object in the response is merged into the trial"""
    r = spec.reset or {}
    if r.get("python"):
        out = await maybe_await(import_ref(r["python"], base_dir)(case, repeat, dict(trial)))
        return out if isinstance(out, dict) else {}
    if r.get("url"):
        import os
        import urllib.request

        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment()

        def render(x):
            if isinstance(x, str):
                return env.from_string(x).render(case_id=case.id, repeat=repeat, trial_id=trial["trial_id"])
            if isinstance(x, dict):
                return {k: render(v) for k, v in x.items()}
            return x
        body = render(r.get("body", {"case_id": "{{ case_id }}", "repeat": "{{ repeat }}", "trial_id": "{{ trial_id }}"}))
        headers = {"Content-Type": "application/json", **{h: os.environ.get(e, "") for h, e in r.get("headers_env", {}).items()}}
        req = urllib.request.Request(r["url"], data=json.dumps(body).encode(), headers=headers, method=r.get("method", "POST"))

        def _do():
            with urllib.request.urlopen(req, timeout=float(r.get("timeout_s", 30))) as resp:  # noqa: S310 - operator-configured URL
                raw = resp.read()
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {}
            return data if isinstance(data, dict) else {}
        return await asyncio.to_thread(_do)
    return {}


@dataclass
class RunContext:
    suite: SuiteSpec
    providers: ProvidersConfig
    pool: ProviderPool
    prompts: PromptStore | None = None
    prompt_overrides: dict[str, str] = field(default_factory=dict)   # {"claims_agent": "1.4.0"}
    extra: dict[str, Any] = field(default_factory=dict)
    #: injected SDK clients by service name ("bedrock-agentcore", "bedrock-agent-runtime", "agent_engine"), for tests
    clients: dict[str, Any] = field(default_factory=dict)


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

    # ---- multi-turn conversations ----------------------------------------
    async def turn(self, case: Case, repeat: int, message: str, conv: dict[str, Any], s: Sample) -> str:
        """One user message of a conversation: send it with conv["session_id"] (and conv["history"]), append what the
        agent did to `s` (trajectory, usage, cost, final_state), return its reply. Targets that implement this get
        scripted (`turns`) and simulated (`goal` / `user_rules`) multi-turn cases through run_conversation."""
        raise NotImplementedError

    def conversational(self, case: Case) -> bool:
        return bool(case.turns or case.goal or case.user_rules) and type(self).turn is not Target.turn

    def new_session_id(self, case: Case, repeat: int) -> str:
        import uuid
        return f"genai-evals-{case.id}-{repeat}-{uuid.uuid4().hex}"[:100]

    async def execute(self, case: Case, repeat: int) -> Sample:
        if self.conversational(case):
            return await self.run_conversation(case, repeat)
        return await self.run(case, repeat)

    async def run_conversation(self, case: Case, repeat: int) -> Sample:
        """Drive a whole conversation through turn(): scripted turns first, then the simulated user (a model from the
        suite's `simulator:` profile with the case's goal and persona, or the case's deterministic `user_rules`), up to
        max_turns (default: the scripted turns, else 6). Every tool call is tagged with the turn it happened in."""
        import time

        from .simulator import simulator_for
        s = self.new_sample(case, repeat)
        conv: dict[str, Any] = {"session_id": self.new_session_id(case, repeat), "history": [], "turn": 1, "data": {}}
        scripted = list(case.turns or [])
        sim = simulator_for(self.ctx, case)
        max_turns = case.max_turns or (len(case.turns) if case.turns and not (case.goal or case.user_rules) else 6)
        if scripted:
            msg: str | None = scripted.pop(0)
        elif case.input is not None and case.input_text():
            msg = case.input_text()
        else:
            msg = await sim.next(case, [], 0) if sim else None
        reply, lat = "", []
        while msg:
            before = len(s.trajectory)
            t0 = time.perf_counter()
            reply = await self.turn(case, repeat, msg, conv, s) or ""
            lat.append((time.perf_counter() - t0) * 1000)
            for tc in s.trajectory[before:]:
                tc.turn = conv["turn"]                      # type: ignore[attr-defined]  (ToolCall allows extras)
            s.messages += [Message(role="user", content=msg), Message(role="assistant", content=reply)]
            conv["history"] += [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
            nxt = scripted.pop(0) if scripted else (await sim.next(case, s.messages, conv["turn"]) if sim else None)
            if nxt and conv["turn"] >= max_turns:
                s.metadata["max_turns_reached"] = True      # the user still had something to say
                break
            msg = nxt
            if msg:
                conv["turn"] += 1
        s.output = reply
        if self.spec.output == "json" and s.output_json is None:
            s.output_json = parse_json(reply)
        s.metadata["turns"] = conv["turn"]
        s.session_id = s.session_id or conv["session_id"]
        # the agent's own time across turns (simulated-user time excluded); per-turn times for p95-style checks
        s.latency_ms = s.latency_ms if s.latency_ms is not None else round(sum(lat), 1)
        s.metadata["turn_latency_ms"] = [round(x, 1) for x in lat]
        return s


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
