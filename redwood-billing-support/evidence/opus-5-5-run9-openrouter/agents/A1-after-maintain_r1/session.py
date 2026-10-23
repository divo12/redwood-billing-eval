"""Per-conversation facts shared by tools, hooks and the agent loop.

The runtime starts one candidate process per conversation, so a module-level session is that
conversation's state. `Agent.get_init_state` calls `reset()` so local test harnesses that run
several conversations in one process start clean too. Everything here is recorded from
observations (tool results, developer answers), never from what the model intends to do.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Session:
    # chunk_id -> {"source", "status", "text"} for every chunk search returned in this conversation
    retrieved: dict[str, dict] = field(default_factory=dict)
    # every confirm_with_developer call, in order: {"proposal", "answer", "message"}
    consents: list[dict] = field(default_factory=list)
    # field -> the developer's answer, for every ask_developer call
    answers: dict[str, str] = field(default_factory=dict)
    # successful console writes: {"tool", "row"}
    receipts: list[dict] = field(default_factory=list)
    # line ids a credit note was attempted for (success or failure) in this conversation
    credit_attempts: set[str] = field(default_factory=set)
    # set once a case is escalated: the console is closed for the rest of the conversation
    handed_off: bool = False

    def last_consent(self) -> dict | None:
        return self.consents[-1] if self.consents else None


SESSION = Session()


def reset() -> Session:
    global SESSION
    SESSION = Session()
    return SESSION


def current() -> Session:
    return SESSION
