"""Per-conversation facts shared by tools and hooks.

The runtime starts a fresh candidate process for every conversation, so module state here
lives exactly as long as one conversation. Only observed outcomes are recorded: developer
confirmations as answered, and writes as returned by the console.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Session:
    confirmations: list[dict] = field(default_factory=list)  # {"proposal", "answer"}
    writes: list[dict] = field(default_factory=list)  # {"tool", "args", "result"}
    handed_off: bool = False
    billing: object | None = None

    def last_confirmation(self) -> dict | None:
        return self.confirmations[-1] if self.confirmations else None

    def approved_since_last_write(self) -> bool:
        """A 'yes' from the developer after the latest successful write of the same kind of action."""
        return bool(self.confirmations) and self.confirmations[-1]["answer"] == "yes"


SESSION = Session()
