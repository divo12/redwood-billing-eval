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
    # Escalated cases need a priority since April 2027; Support's standard for billing handoffs is
    # `normal` (record format changes page -> Dev Patel's #support announcement of 2027-03-30).
    case_priority: str = "normal"
    billing: object | None = None

    def last_confirmation(self) -> dict | None:
        return self.confirmations[-1] if self.confirmations else None

    def approved_since_last_write(self) -> bool:
        """A 'yes' from the developer after the latest successful write of the same kind of action."""
        return bool(self.confirmations) and self.confirmations[-1]["answer"] == "yes"


SESSION = Session()
