"""The simulated developer: redwood_kit.customer, shared with the builder's practice runner.

The developer writes the scenario's opening, then one agenda message per agent reply, whatever the
agent said. It answers `ask_developer` from the scenario's facts and `confirm_with_developer` with
the scenario's fixed answer, and logs both. Once the conversation has been handed to a team it ends
with a transfer (the console is closed by then); otherwise it ends with a stop when the agenda is used up.
"""

from __future__ import annotations

from redwood_kit.customer import (
    APPROVE_REPLY,
    DECLINE_REPLY,
    OUT_OF_SCOPE_TOKEN,
    SAME_FACT,
    STOP_MESSAGE,
    STOP_TOKENS,
    TRANSFER_MESSAGE,
    TRANSFER_TOKEN,
    UNKNOWN_FACT,
    Customer,
    DeveloperCall,
    Speaker,
)

__all__ = [
    "APPROVE_REPLY", "DECLINE_REPLY", "OUT_OF_SCOPE_TOKEN", "SAME_FACT", "STOP_MESSAGE", "STOP_TOKENS",
    "TRANSFER_MESSAGE", "TRANSFER_TOKEN", "UNKNOWN_FACT", "Customer", "DeveloperCall", "Speaker",
]
