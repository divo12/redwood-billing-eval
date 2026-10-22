"""The simulated developer: a fixed script, no model.

The developer writes the scenario's opening, then one agenda message per agent reply, whatever the
agent said. It answers `ask_developer` from the scenario's facts and `confirm_with_developer` with
the scenario's fixed answer, and logs both. Once the conversation has been handed to a team it ends
with a transfer (the console is closed by then); otherwise it ends with a stop when the agenda is used up.
"""

from __future__ import annotations

from typing import Protocol

from grade.scenarios import CustomerScript
from pydantic import BaseModel, ConfigDict
from redwood_kit.developer import FIELDS
from redwood_kit.jsontypes import Json, JsonObject
from redwood_kit.messages import UserMessage

TRANSFER_TOKEN = "###TRANSFER###"
OUT_OF_SCOPE_TOKEN = "###OUT-OF-SCOPE###"
STOP_TOKENS = ("###STOP###", TRANSFER_TOKEN, OUT_OF_SCOPE_TOKEN)
UNKNOWN_FACT = "I don't have that."
DECLINE_REPLY = "No, please don't do that."
APPROVE_REPLY = "Yes, go ahead."
STOP_MESSAGE = "Thanks, that's what I needed. ###STOP###"
# Fields that name the same fact: asking for either gets the developer's answer to whichever the script has.
SAME_FACT = {"invoice_month": "billing_period", "billing_period": "invoice_month"}
TRANSFER_MESSAGE = "Thanks for passing it on. ###TRANSFER###"


class DeveloperCall(BaseModel):
    """One `ask_developer` or `confirm_with_developer` call and the developer's answer.

    `order` is how many console calls had run before it, so a write at console log index `i`
    came after this call exactly when `order <= i`.
    """

    model_config = ConfigDict(frozen=True)

    op: str
    order: int
    fields: list[str] = []
    proposal: str = ""
    answer: JsonObject = {}


class Speaker(Protocol):
    """Anything that can play the developer; the scripted `Customer` or a test double."""

    calls: list[DeveloperCall]

    def reply(self, agent_text: str, handed_off: bool) -> UserMessage: ...

    def handle(self, request: JsonObject, order: int) -> JsonObject: ...


class Customer:
    """The developer, played from a scenario's `customer` script."""

    def __init__(self, script: CustomerScript) -> None:
        self.script = script
        self.sent = 0  # messages written so far: the opening, then agenda items
        self.calls: list[DeveloperCall] = []

    def reply(self, agent_text: str, handed_off: bool) -> UserMessage:
        """The next message: the opening, the next agenda item, or the ending."""
        del agent_text  # the script does not depend on what the agent wrote
        if handed_off:
            return UserMessage(content=TRANSFER_MESSAGE)
        messages = (self.script.opening, *self.script.agenda)
        if self.sent < len(messages):
            self.sent += 1
            return UserMessage(content=messages[self.sent - 1])
        return UserMessage(content=STOP_MESSAGE)

    def handle(self, request: JsonObject, order: int) -> JsonObject:
        """Answer one developer call from the agent's tools."""
        match request.get("op"):
            case "ask":
                return self._ask(request.get("fields"), order)
            case "confirm":
                return self._confirm(request.get("proposal"), order)
            case other:
                raise ValueError(f"unknown developer op {other!r}")

    def _ask(self, fields: Json, order: int) -> JsonObject:
        if not isinstance(fields, list) or not all(isinstance(f, str) and f in FIELDS for f in fields):
            raise ValueError(f"fields must be a list drawn from {list(FIELDS)}")
        names = [f for f in fields if isinstance(f, str)]
        facts = self.script.facts
        answers: JsonObject = {name: facts.get(name) or facts.get(SAME_FACT.get(name, ""), UNKNOWN_FACT)
                               for name in names}
        self.calls.append(DeveloperCall(op="ask", order=order, fields=names, answer={"answers": answers}))
        return {"answers": answers}

    def _confirm(self, proposal: Json, order: int) -> JsonObject:
        if not isinstance(proposal, str) or not proposal.strip():
            raise ValueError("proposal must say what you will do")
        default = APPROVE_REPLY if self.script.confirm == "yes" else DECLINE_REPLY
        answer: JsonObject = {"answer": self.script.confirm, "message": self.script.confirm_reply or default}
        self.calls.append(DeveloperCall(op="confirm", order=order, proposal=proposal, answer=answer))
        return answer
