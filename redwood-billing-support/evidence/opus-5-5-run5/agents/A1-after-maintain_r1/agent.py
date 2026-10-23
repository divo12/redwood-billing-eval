from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from redwood_kit.agent_context import get_agent_context
from redwood_kit.messages import AssistantMessage, SystemMessage, UserMessage
from redwood_kit.skills import load_skills
from redwood_kit.subagents import load_subagents

HERE = Path(__file__).parent
MODEL = "claude-sonnet-5-5"

# A reply that asks the developer for facts or approval in chat text gets no answer: the developer
# answers only through ask_developer / confirm_with_developer. Such a draft is sent back once.
CHAT_ASK = re.compile(  # requests that need no question mark to be a request
    r"please (?:share|provide|send|confirm|let me know|reply with)|(?:let me know|tell me) (?:your|the|which|if you)",
    re.I,
)
CHAT_QUESTION = re.compile(  # phrasings that are a request when the reply asks a question
    r"(?:could|can|would) you (?:please )?(?:share|provide|send|confirm|tell me|give me|check|let me know)"
    r"|what(?:'s| is| are) (?:your|the) (?:org|account|invoice|request|plan|billing|error|decline)"
    r"|which (?:invoice|line|month|request|org|account|card)"
    r"|would you like me to|shall i |do you want me to|should i (?:go ahead|proceed|open|hand|escalate)",
    re.I,
)


def asks_in_chat(text: str) -> bool:
    return bool(CHAT_ASK.search(text)) or ("?" in text and bool(CHAT_QUESTION.search(text)))


NUDGE = (
    "[internal check, not from the developer] Your draft asks the developer something in chat. They cannot "
    "answer chat questions. If you need a fact, call ask_developer with the fields; if you need approval for an "
    "action, call confirm_with_developer with exactly what you will do. If nothing is actually needed, rewrite "
    "the reply without the question."
)


class Agent:
    def __init__(self, context):
        self.gateway = context.model_gateway
        self.tools = context.action_interface.available
        instructions = (HERE / "system_prompt.md").read_text().strip()
        if text := (HERE / "AGENTS.md").read_text().strip():
            instructions += f"\n\n# Operating principles\n\n{text}"
        if skills := load_skills(HERE):
            lines = "\n".join(f"- {s.name}: {s.description}" for s in skills.values())
            instructions += f"\n\n# Skills\n\nCall load_skill with the name when one applies, then follow it.\n\n{lines}"
        if subagents := load_subagents(HERE):
            lines = "\n".join(f"- {s.name}: {s.description}" for s in subagents.values())
            instructions += (f"\n\n# Subagents\n\nCall delegate with the name and a self-contained task "
                             f"(it sees nothing else of the conversation).\n\n{lines}")
        self.instructions = instructions

    def _system(self) -> SystemMessage:
        now = datetime.now(timezone.utc)
        return SystemMessage(f"{self.instructions}\n\nCurrent date and time (UTC): {now:%Y-%m-%d %H:%M}.")

    def get_init_state(self, message_history=None):
        return {"messages": list(message_history or []), "nudged": False}

    def _generate(self, messages):
        last_error = None
        for _ in range(2):
            try:
                return self.gateway.generate(model=MODEL, messages=[self._system(), *messages],
                                             actions=self.tools or None)
            except Exception as error:  # noqa: BLE001 - one retry, then a safe reply instead of a crash
                last_error = error
        print(f"model call failed: {last_error}")
        return AssistantMessage(content="Sorry, I hit a problem on my side while working on this. "
                                        "Could you send your last message again?")

    def generate_next_message(self, message, state):
        if isinstance(state, list):  # tolerate a bare history
            state = {"messages": state, "nudged": False}
        messages = [*state["messages"], message]
        nudged = False if isinstance(message, UserMessage) else state.get("nudged", False)
        reply = self._generate(messages)
        if not reply.is_tool_call() and not nudged and asks_in_chat(reply.content or ""):
            nudged = True
            retry = self._generate([*messages, reply, UserMessage(content=NUDGE)])
            if retry.is_tool_call() or (retry.content or "").strip():
                reply = retry
        return reply, {"messages": [*messages, reply], "nudged": nudged}


def create_agent():
    return Agent(get_agent_context())
