"""LLM user simulator for multi-turn agent evals (persona + goal card)."""
from __future__ import annotations

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
