"""The scripted developer, with each scripted line written in the developer's own words by a model.

Code still decides everything the grade depends on: which line is said on which turn (the script's
order), the facts `ask_developer` returns, the fixed `confirm_with_developer` answer, and when the
conversation ends (a handoff or the end of the agenda). The model only rewords the line due this turn as
a reply to what support just wrote, with the baseline customer rules of the reference simulator and no
behaviour persona. A rewording that drops a number or identifier from the line, or fails, is replaced
by the scripted line itself, so a model fault never changes or drops a case.

    REDWOOD_CUSTOMER_MODEL   model that writes the turns (e.g. gpt-6-luna); unset: the plain script
    REDWOOD_CUSTOMER_CACHE   JSON-lines file of written turns; a regrade of the same transcript reuses them
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from grade.scenarios import Scenario
from redwood_kit.messages import UserMessage
from run import openai_gateway
from run.customer import APPROVE_REPLY, DECLINE_REPLY, STOP_TOKENS, Customer, DeveloperCall

CUSTOMER_MODEL = "REDWOOD_CUSTOMER_MODEL"
CUSTOMER_CACHE = "REDWOOD_CUSTOMER_CACHE"

RULES = """You are the developer who contacted Redwood Inference billing support. \
Write the way this person writes: {persona}

YOUR MESSAGE THIS TURN must say everything in this line, in your own words, as a reply to support's last message. \
Keep every number, ID, date, time, amount and name in it exactly as written. Leave nothing out and add no new request:
"{line}"

Your private note (support can't see it). These facts are NOT in the line above; add one only when support's \
last message asks for it, and otherwise keep them to yourself:
{facts}

Rules:
- If support's last message asks you to approve or confirm an action, also give this answer to it: "{approval}"
- If support asks for something that is neither in the line nor in your note, say you don't have it.
- If support states a detail about your account or problem that contradicts your note or what you said, correct it.
- Don't mention team names, priority codes or how the ticket should be filed, and don't guess Redwood's policies.
- Write one to four sentences. Reply with only your message."""

# Identifiers with a digit (invoices, requests, lines) and numbers: a rewording must keep every one of them.
KEEP = re.compile(r"[A-Za-z]+[-_][\w-]*\d[\w-]*|\d[\d,.:]*\d|\d")


class _Cache:
    """Written turns by a hash of everything that decided them; shared by the worker threads of one run."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.turns: dict[str, str] = {}
        if self.path is not None and self.path.exists():
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                self.turns[row["key"]] = row["text"]

    def get(self, key: str) -> str | None:
        with self.lock:
            return self.turns.get(key)

    def put(self, key: str, text: str, record: dict[str, object]) -> None:
        with self.lock:
            self.turns[key] = text
            if self.path is not None:
                with self.path.open("a") as file:
                    file.write(json.dumps({"key": key, "text": text, **record}) + "\n")


_CACHES: dict[str, _Cache] = {}
_CACHES_LOCK = threading.Lock()


def _cache() -> _Cache:
    path = os.environ.get(CUSTOMER_CACHE, "")
    with _CACHES_LOCK:
        return _CACHES.setdefault(path, _Cache(path or None))


def keeps_every_value(line: str, written: str, org: str = "") -> bool:
    """The rewording still carries every number and identifier the scripted line had, and the org if it named it."""
    plain = written.lower().replace(",", "")
    values = [*KEEP.findall(line), *([org] if org and org in line else [])]
    return all(value.lower().replace(",", "") in plain for value in values)


class WrittenCustomer(Customer):
    """The scenario's script, each opening and agenda line written by `model` in the developer's words."""

    def __init__(self, case: Scenario, model: str) -> None:
        super().__init__(case.customer)
        self.case_id, self.persona, self.model = case.id, case.brief.persona, model
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

    def _instructions(self, line: str) -> str:
        facts = "\n".join(f"  - {name}: {value}" for name, value in self.script.facts.items()) or "  - nothing else"
        # The same answer confirm_with_developer gives; consent still counts only through that tool.
        approval = self.script.confirm_reply or (APPROVE_REPLY if self.script.confirm == "yes" else DECLINE_REPLY)
        return RULES.format(persona=self.persona or "a developer", facts=facts, line=line, approval=approval)

    def _reword(self, line: str, instructions: str, conversation: str) -> str:
        """The model's wording of `line`, or the line itself when the call fails or drops a value."""
        try:
            text = " ".join((openai_gateway.complete(self.model, instructions, conversation) or "").split())
        except Exception:  # noqa: BLE001 - any model fault falls back to the scripted line
            return line
        org = self.script.facts.get("org_id", "")
        return text if text and "###" not in text and keeps_every_value(line, text, org) else line

    def _write(self, line: str) -> str:
        instructions, conversation = self._instructions(line), "\n".join(self.transcript)
        key = hashlib.sha256(json.dumps([self.case_id, self.model, instructions, conversation]).encode()).hexdigest()
        cache = _cache()
        written = cache.get(key)
        if written is None:
            written = self._reword(line, instructions, conversation)
            cache.put(key, written, {"case": self.case_id, "line": line, "fallback": written == line})
        self.calls.append(DeveloperCall(op="say", order=-1, proposal=line,
                                        answer={"message": written, "fallback": written == line}))
        return written
