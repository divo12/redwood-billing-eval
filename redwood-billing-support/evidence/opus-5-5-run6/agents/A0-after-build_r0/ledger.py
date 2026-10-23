"""Per-conversation facts shared by tools and hooks.

The runtime starts a fresh agent process for every conversation, so module state here lives exactly
one conversation. Only observed results are recorded (tool results, developer answers), never intents.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ledger:
    # source id -> concatenated text of every chunk search returned for it in this conversation
    retrieved: dict[str, str] = field(default_factory=dict)
    # (proposal, answer) for each confirm_with_developer call, in order
    consents: list[tuple[str, str]] = field(default_factory=list)
    # the consent index each write used; one approval authorises one set of related writes
    used_consent: int = -1
    # receipts of successful writes
    writes: list[dict] = field(default_factory=list)
    escalated: bool = False
    # while a subagent runs: the tool names it may use (None = the main agent is acting)
    delegating: tuple[str, ...] | None = None
    # the toolkit instance, so hooks can read the console
    toolkit: object | None = None

    def note_results(self, results: list[dict]) -> None:
        for row in results:
            text = self.retrieved.get(row["source"], "")
            if row["text"] not in text:
                self.retrieved[row["source"]] = f"{text}\n{row['text']}"

    def latest_consent(self) -> tuple[int, str, str] | None:
        if not self.consents:
            return None
        index = len(self.consents) - 1
        proposal, answer = self.consents[index]
        return index, proposal, answer


LEDGER = Ledger()
