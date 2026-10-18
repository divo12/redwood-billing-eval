"""Typed client interface to the Redwood billing record store."""
from __future__ import annotations

import json
import socket
from collections.abc import Callable
from enum import StrEnum

from ..jsontypes import Json, JsonObject, is_json_object

PRACTICE_SOCKET = "/run/redwood/practice-console.sock"


class Op(StrEnum):
    DESCRIBE = "describe"
    QUERY = "query"
    INSERT = "insert"
    UPDATE = "update"


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELDS = "missing_fields"
    NOT_FOUND = "not_found"
    READ_ONLY = "read_only"
    NOT_DRAFT = "not_draft"
    CLOSED = "closed"
    BUSINESS_RULE = "business_rule"


class BillingError(RuntimeError):
    """An error returned by the billing console.

    `code` is the console's ErrorCode, or None when the request never reached the console.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        prefix = message.partition(": ")[0]
        self.code = ErrorCode(prefix) if prefix in set(ErrorCode) else None


class Billing:
    def __init__(self, send: Callable[[JsonObject], JsonObject]) -> None:
        self._send = send

    def _call(self, op: Op, **fields: Json) -> JsonObject:
        try:
            reply = self._send({"call": "billing", "op": op, **fields})
        except RuntimeError as error:
            raise BillingError(str(error)) from None
        return {key: value for key, value in reply.items() if key != "ok"}

    def describe(self) -> JsonObject:
        """Return table and field descriptors."""
        return self._call(Op.DESCRIBE)

    def query(self, table: str, where: JsonObject | None = None, cursor: str | None = None) -> JsonObject:
        """Return a page of rows matching field values."""
        return self._call(Op.QUERY, table=table, where=where, cursor=cursor)

    def insert(self, table: str, row: JsonObject) -> JsonObject:
        """Insert a row and return the stored record."""
        return self._call(Op.INSERT, table=table, row=row)

    def update(self, table: str, row_id: str, changes: JsonObject) -> JsonObject:
        """Update a row and return the stored record."""
        return self._call(Op.UPDATE, table=table, row_id=row_id, changes=changes)


def reply_body(reply: object) -> JsonObject:
    """Return a console reply's body, or raise its error for `Billing` to rewrap."""
    if not is_json_object(reply):
        raise RuntimeError("practice console sent a malformed reply")
    if reply.get("ok") is not True:
        raise RuntimeError(str(reply.get("error", "practice console rejected the request")))
    return reply


def development_billing(socket_path: str = PRACTICE_SOCKET) -> Billing:
    """A fresh practice console over synthetic records for the current release; writes are discarded."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(socket_path)
    stream = connection.makefile("rw", encoding="utf-8")

    def send(request: JsonObject) -> JsonObject:
        stream.write(json.dumps(request) + "\n")
        stream.flush()
        return reply_body(json.loads(stream.readline() or "null"))

    return Billing(send)
