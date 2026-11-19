"""Simulated users for multi-turn agent evals: an LLM with a persona and goal card, or deterministic rules."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models import Case, Message
from .base import parse_json

if TYPE_CHECKING:
    from .base import RunContext

SIM_PROMPT = """<task>simulate_user</task>
You are role-playing a customer talking to a support agent. Stay in character.
<persona>{persona}</persona>
<goal>{goal}</goal>
<turn>{turn}</turn>
<conversation>
{transcript}
</conversation>
Write the customer's next message. If the goal is achieved or the conversation should end, reply with [DONE].
Respond as JSON: {{"message": "<next customer message or [DONE]>"}}"""


class UserSimulator:
    def __init__(self, provider, seed: int = 7):
        self.provider = provider
        self.seed = seed

    @classmethod
    def for_suite(cls, ctx: RunContext) -> UserSimulator | None:
        if not ctx.suite.simulator:
            return None
        return cls(ctx.pool.get(ctx.suite.simulator), ctx.suite.seed)

    async def next(self, case: Case, messages: list[Message], turn: int) -> str | None:
        transcript = "\n".join(f"{'CUSTOMER' if m.role == 'user' else 'AGENT'}: {m.content}" for m in messages
                               if m.role in ("user", "assistant") and m.content)
        prompt = SIM_PROMPT.format(persona=case.persona or "an ordinary customer", goal=case.goal or case.input_text(),
                                   turn=turn, transcript=transcript)
        r = await self.provider.complete([Message(role="user", content=prompt)], temperature=0.7, json_mode=True)
        data = parse_json(r.text) or {}
        msg = (data.get("message") if isinstance(data, dict) else None) or r.text.strip()
        return None if not msg or "[DONE]" in msg else msg


class RulesSimulator:
    """A deterministic simulated user: each rule {when: <regex on the agent's last reply>, say: <message>} fires once,
    in order; {otherwise: <message>} applies when none matches (default: end). Reproducible, so it runs offline and
    in CI, and still reacts to what the agent said (it answers the agent's question rather than following a script)."""

    def __init__(self, rules: list[dict]):
        self.rules = rules
        self.fired: list[int] = []            # one simulator per conversation, so this is per trial

    async def next(self, case: Case, messages: list[Message], turn: int) -> str | None:
        last = next((m.content or "" for m in reversed(messages) if m.role == "assistant"), "")
        for i, r in enumerate(self.rules):
            if "when" in r and i not in self.fired and re.search(r["when"], last, re.I):
                self.fired.append(i)
                say = r.get("say", "[DONE]")
                return None if "[DONE]" in say else say
        other = next((r["otherwise"] for r in self.rules if "otherwise" in r), None)
        return None if not other or "[DONE]" in other else other


def simulator_for(ctx, case: Case):
    """Rules when the case has user_rules and the suite's simulator is unset or "rules"; the LLM simulator when the
    case has a goal and the suite names a simulator profile; otherwise none (scripted turns only)."""
    sim_profile = ctx.suite.simulator
    if case.user_rules and sim_profile in (None, "", "rules"):
        return RulesSimulator(case.user_rules)
    if case.goal and sim_profile and sim_profile != "rules":
        return UserSimulator(ctx.pool.get(sim_profile), ctx.suite.seed)
    return None
