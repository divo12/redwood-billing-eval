"""The simulated developer: a model plays the customer from a hidden scenario brief."""

from __future__ import annotations

import json
from typing import Protocol

from grade.scenarios import CustomerBrief
from redwood_kit.jsontypes import JsonObject
from redwood_kit.messages import AssistantMessage, Message, SystemMessage, UserMessage
from run import gateway

TRANSFER_TOKEN = "###TRANSFER###"
OUT_OF_SCOPE_TOKEN = "###OUT-OF-SCOPE###"
STOP_TOKENS = ("###STOP###", TRANSFER_TOKEN, OUT_OF_SCOPE_TOKEN)
CUSTOMER_MODEL = "gpt-5.6-sol"
CUSTOMER_KWARGS: JsonObject = {}  # the API default effort (medium)


class CustomerError(RuntimeError):
    """The simulated developer failed: an invalid run, not an agent failure."""


class Speaker(Protocol):
    """Anything that can play the developer; the real `Customer` or a test double."""

    def reply(self, agent_text: str) -> Message: ...


class Customer:
    """The developer, played by a model from a hidden scenario.

    Roles are flipped: the agent's replies are this model's user turns.
    """

    def __init__(self, brief: CustomerBrief, guidelines: str) -> None:
        # ponytail: the scenario goes in as JSON; render it as prose sections if the model misreads it
        self.system = SystemMessage(
            f"{guidelines}\n<scenario>\n{json.dumps(brief.model_dump(mode='json'), indent=2)}\n</scenario>")
        self.history: list[Message] = []

    def reply(self, agent_text: str) -> UserMessage:
        self.history.append(UserMessage(content=agent_text))
        try:
            text = gateway.generate(CUSTOMER_MODEL, [self.system, *self.history], None, None, **CUSTOMER_KWARGS).content
        except Exception as error:
            raise CustomerError(f"{type(error).__name__}: {error}") from error
        self.history.append(AssistantMessage(content=text))
        return UserMessage(content=text)
