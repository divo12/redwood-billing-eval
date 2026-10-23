"""The simulated developer: a fixed script, optionally worded by a model.

The developer writes the script's opening, then one agenda message per agent reply, whatever the
agent said. It answers `ask_developer` from the script's facts and `confirm_with_developer` with the
script's fixed answer, and logs both. Once the conversation has been handed to a team it ends with a
transfer (the console is closed by then); otherwise it ends with a stop when the agenda is used up.

`WrittenCustomer` has a model word each opening and agenda line as a reply to what support just
wrote. Code still decides everything else: which line is said on which turn, the facts, the consent
answer and the ending. The model never sees the facts, so it cannot answer a question asked in chat:
the developer answers only through `ask_developer`. A wording that drops a number or identifier, or
fails, is replaced by the scripted line itself.

Grading and practice conversations both use this module, so a practice developer behaves like a
graded one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from .developer import FIELDS
from .jsontypes import Json, JsonObject
from .messages import UserMessage

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


class CustomerScript(BaseModel):
    """The scripted developer: what they write, in order, and what they answer when asked.

        opening   the first message, after the agent's greeting
        agenda    the next messages, one per agent reply, whatever the agent said
        facts     answers to `ask_developer`, by field; any other field gets "I don't have that"
        confirm   the answer to every `confirm_with_developer`; `confirm_reply` is said with it

    Once the conversation is handed to a team the developer ends it with a transfer on the agent's next
    reply; otherwise they end it with a stop when the agenda is used up. So every question the agent must
    answer from the console is asked before the handoff the scenario expects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    opening: str
    agenda: tuple[str, ...] = ()
    facts: dict[str, str] = {}
    confirm: Literal["yes", "no"] = "yes"
    confirm_reply: str = ""

    @model_validator(mode="after")
    def _facts_use_the_field_vocabulary(self) -> CustomerScript:
        unknown = sorted(set(self.facts) - set(FIELDS))
        if unknown:
            raise ValueError(f"facts use fields outside redwood_kit.developer.FIELDS: {unknown}")
        return self


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
    """The developer, played from a `CustomerScript`."""

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


RULES = """You are the developer who contacted Redwood Inference billing support. \
Write the way this person writes: {persona}

YOUR MESSAGE THIS TURN must say everything in this line, in your own words, as a reply to support's last message. \
Keep every number, ID, date, time, amount and name in it exactly as written. Leave nothing out and add no new request:
"{line}"

Rules:
- You answer support's questions only through their form, never in chat. If support's last message asks you a \
question in chat, don't answer it and don't say you lack the answer; just say your line.
- If support's last message asks you to approve or confirm an action, also give this answer to it: "{approval}"
- If support repeats something you said earlier in a different way that changes its meaning, correct it.
- Don't mention team names, priority codes or how the ticket should be filed, and don't guess Redwood's policies.
- Write one to four sentences. Reply with only your message."""

# Identifiers with a digit (invoices, requests, lines) and numbers: a wording must keep every one of them.
KEEP = re.compile(r"[A-Za-z]+[-_][\w-]*\d[\w-]*|\d[\d,.:]*\d|\d")

# (instructions, conversation so far) -> the model's text, or None. Raising counts as a failed call.
Complete = Callable[[str, str], "str | None"]


def instructions(persona: str, line: str, approval: str) -> str:
    """The wording model's instructions for one line."""
    return RULES.format(persona=persona or "a developer", line=line, approval=approval)


def keeps_every_value(line: str, written: str, org: str = "") -> bool:
    """The wording still carries every number and identifier the scripted line had, and the org if it named it."""
    plain = written.lower().replace(",", "")
    values = [*KEEP.findall(line), *([org] if org and org in line else [])]
    return all(value.lower().replace(",", "") in plain for value in values)


class WrittenCustomer(Customer):
    """The script, each opening and agenda line worded by a model in the developer's words."""

    def __init__(self, script: CustomerScript, persona: str, complete: Complete | None = None) -> None:
        super().__init__(script)
        self.persona, self.complete = persona, complete
        self.transcript: list[str] = []

    def reply(self, agent_text: str, handed_off: bool) -> UserMessage:
        scripted = super().reply(agent_text, handed_off)
        self.transcript.append(f"Support: {agent_text}")
        line = scripted.content or ""
        if any(token in line for token in STOP_TOKENS):  # endings stay exactly as scripted
            return scripted
        written = self._write(line)
        self.transcript.append(f"You: {written}")
        return UserMessage(content=written)

    def _approval(self) -> str:
        # The same answer confirm_with_developer gives; consent still counts only through that tool.
        return self.script.confirm_reply or (APPROVE_REPLY if self.script.confirm == "yes" else DECLINE_REPLY)

    def _instructions(self, line: str) -> str:
        return instructions(self.persona, line, self._approval())

    def _model_text(self, line: str, instructions: str, conversation: str) -> str | None:
        """The wording model's raw answer for `line`; a subclass may send the parts instead of the instructions."""
        del line
        if self.complete is None:
            raise RuntimeError("no wording model")
        return self.complete(instructions, conversation)

    def _reword(self, line: str, instructions: str, conversation: str) -> str:
        """The model's wording of `line`, or the line itself when the call fails or drops a value."""
        try:
            text = " ".join((self._model_text(line, instructions, conversation) or "").split())
        except Exception:  # noqa: BLE001 - any model fault falls back to the scripted line
            return line
        org = self.script.facts.get("org_id", "")
        return text if text and "###" not in text and keeps_every_value(line, text, org) else line

    def _wording(self, line: str, instructions: str, conversation: str) -> str:
        """Where a subclass can cache wordings; by default every line is worded afresh."""
        return self._reword(line, instructions, conversation)

    def _write(self, line: str) -> str:
        written = self._wording(line, self._instructions(line), "\n".join(self.transcript))
        self.calls.append(DeveloperCall(op="say", order=-1, proposal=line,
                                        answer={"message": written, "fallback": written == line}))
        return written
