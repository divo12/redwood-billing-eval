"""The developer on the other end of a support conversation, as tools the agent can call.

The developer answers questions about named facts (`ask_developer`) and says yes or no to a
proposed action (`confirm_with_developer`). Both go to the host, which answers for the developer.
Facts the developer has not written in the chat come only through `ask_developer`.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, get_args

from .jsontypes import Json, JsonObject
from .toolkit import ToolType, is_tool

Field = Literal[
    "org_id",
    "invoice_id",
    "invoice_month",
    "line_items",
    "amounts",
    "request_ids",
    "response_status",
    "time_of_issue",
    "checks_done",
    "plan",
    "contract",
    "error_code",
    "billing_period",
    "timezone",
]
FIELDS: tuple[str, ...] = get_args(Field)


class Developer:
    """Client for the host's `developer` calls."""

    def __init__(self, send: Callable[[JsonObject], JsonObject]) -> None:
        self._send = send

    def _call(self, op: str, **fields: Json) -> JsonObject:
        reply = self._send({"call": "developer", "op": op, **fields})
        return {key: value for key, value in reply.items() if key != "ok"}

    def ask(self, fields: list[str]) -> JsonObject:
        return self._call("ask", fields=list[Json](fields))

    def confirm(self, proposal: str) -> JsonObject:
        return self._call("confirm", proposal=proposal)


class DeveloperTools:
    """Mix into `Tools` to let the model ask the developer for facts and for consent."""

    developer: Developer | None = None  # set by the runtime, like ToolKitBase.billing

    def _developer(self) -> Developer:
        if self.developer is None:
            raise RuntimeError("no developer is connected to this conversation")
        return self.developer

    @is_tool(ToolType.READ)
    def ask_developer(self, fields: list[Field]) -> JsonObject:
        """Ask the developer for facts they have not written in the chat yet.

        Args:
            fields: The facts you need. Each answer is the developer's own words, or that they don't have it.
        """
        return self._developer().ask(list(fields))

    @is_tool(ToolType.READ)
    def confirm_with_developer(self, proposal: str) -> JsonObject:
        """Ask the developer to approve one specific action before you take it.

        Args:
            proposal: Exactly what you will do, e.g. which invoice line you will credit and for how much.
        """
        return self._developer().confirm(proposal)
