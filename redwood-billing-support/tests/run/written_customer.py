"""The scripted developer, with each scripted line written in the developer's own words by a model.

Code still decides everything the grade depends on: which line is said on which turn (the script's
order), the facts `ask_developer` returns, the fixed `confirm_with_developer` answer, and when the
conversation ends (a handoff or the end of the agenda). The model only rewords the line due this turn as
a reply to what support just wrote, with the baseline customer rules of the reference simulator and no
behaviour persona. The model never sees the script's facts, so it cannot answer a question asked in chat: as
the agent contract says, the developer answers only through `ask_developer`. A rewording that drops a number
or identifier from the line, or fails, is replaced
by the scripted line itself, so a model fault never changes or drops a case.

    USER_MODEL               model that writes the turns (a LiteLLM id, e.g. openai/gpt-6-luna); unset: the plain script
    REDWOOD_CUSTOMER_CACHE   JSON-lines file of written turns; a regrade of the same transcript reuses them
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from grade.scenarios import Scenario
from redwood_kit import customer as shared
from redwood_kit.customer import KEEP, RULES, keeps_every_value  # noqa: F401 - the grader's names for them
from run import gateway

CUSTOMER_MODEL = gateway.USER_MODEL
CUSTOMER_CACHE = "REDWOOD_CUSTOMER_CACHE"


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


class WrittenCustomer(shared.WrittenCustomer):
    """The scenario's script, each opening and agenda line written by `model`, with a cache shared by a run."""

    def __init__(self, case: Scenario, model: str) -> None:
        # Looked up at call time, so tests can stand in for the provider.
        super().__init__(case.customer, case.brief.persona,
                         lambda instructions, text: gateway.complete(model, instructions, text))
        self.case_id, self.model = case.id, model

    def _wording(self, line: str, instructions: str, conversation: str) -> str:
        key = hashlib.sha256(json.dumps([self.case_id, self.model, instructions, conversation]).encode()).hexdigest()
        cache = _cache()
        written = cache.get(key)
        if written is None:
            written = self._reword(line, instructions, conversation)
            cache.put(key, written, {"case": self.case_id, "line": line, "fallback": written == line})
        return written
