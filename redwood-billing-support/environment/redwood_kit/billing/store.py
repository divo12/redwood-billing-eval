"""Public billing console facade, request validation, persistence, replay, and projection."""
from __future__ import annotations

import argparse
import base64
import copy
import grp
import itertools
import json
import os
import re
import socketserver
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from ..jsontypes import Json, JsonObject, is_json_object
from .client import PRACTICE_SOCKET, ErrorCode, Op, reply_body
from .client import Billing as Client
from .schema import (
    ID_FIELDS,
    PAGE_SIZE,
    SCHEMA,
    TABLES,
    FieldSpec,
    NoDefault,
    Release,
    State,
    Table,
    TableSpec,
)
from .schema import (
    optional_text as _optional_text,
)
from .schema import (
    text as _text,
)
from .schema import (
    texts as _texts,
)
from .validation import check_row, check_rules, check_value, validate

__all__ = [
    "Billing",
    "BillingError",
    "ConsoleCall",
    "Handoff",
    "ID_FIELDS",
    "PAGE_SIZE",
    "PROJECTION",
    "SCHEMA",
    "TABLES",
    "empty",
    "local_console",
    "open_practice_server",
    "practice_seed",
    "project",
    "replay",
    "state_from_json",
    "validate",
]


class ConsoleCall(BaseModel):
    """One console request: what was asked, whether it worked, and what it read or wrote."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    at: int
    op: str | None = None
    table: str | None = None
    args: JsonObject = {}
    ok: bool = False
    account_ids: list[str] | None = None
    row: JsonObject | None = None
    error: str | None = None


def parse_money(value: Json, field: str) -> int:
    if not isinstance(value, dict) or set(value) != {"value", "currency"}:
        raise ValueError(f"{field} must be money with a two-decimal USD value")
    currency = value["currency"]
    amount_text = value["value"]
    if currency != "USD" or not isinstance(amount_text, str):
        raise ValueError(f"{field} must be money with a two-decimal USD value")
    if re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d{2}", amount_text) is None:
        raise ValueError(f"{field} must be money with a two-decimal USD value")
    negative = amount_text.startswith("-")
    whole, cents = amount_text.lstrip("-").split(".")
    amount = int(whole) * 100 + int(cents)
    return -amount if negative else amount


def format_money(cents: int) -> JsonObject:
    sign = "-" if cents < 0 else ""
    amount = abs(cents)
    return {"value": f"{sign}{amount // 100}.{amount % 100:02d}", "currency": "USD"}


class BillingError(RuntimeError):
    """The record-store request was rejected; `code` says why in a stable, matchable form."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.INVALID_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class Handoff(NamedTuple):
    team: str
    case_id: str


@dataclass(frozen=True)
class QueryRequest:
    table: Json
    where: Json
    cursor: Json


@dataclass(frozen=True)
class InsertRequest:
    table: Json
    row: Json


@dataclass(frozen=True)
class UpdateRequest:
    table: Json
    row_id: Json
    changes: Json


@dataclass(frozen=True)
class OpSignature:
    display_name: str
    duplicate_name: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    injected: tuple[str, ...]


@dataclass
class ConsoleLogEntry:
    at: int
    op: Json
    table: Json
    args: JsonObject
    ok: bool = False
    account_ids: list[str] | None = None
    row: JsonObject | None = None
    error: str | None = None

    def to_call(self) -> ConsoleCall:
        return ConsoleCall(
            at=self.at,
            op=self.op if isinstance(self.op, str) else None,
            table=self.table if isinstance(self.table, str) else None,
            args=self.args,
            ok=self.ok,
            account_ids=self.account_ids,
            row=self.row,
            error=self.error,
        )


@dataclass(frozen=True)
class ConsoleResult:
    value: JsonObject
    account_ids: list[str] | None = None


QUERY_SIGNATURE = OpSignature(
    "Billing._query",
    "redwood_kit.billing_store.Billing._query",
    ("table",),
    ("where", "cursor"),
    ("canonical",),
)
INSERT_SIGNATURE = OpSignature(
    "Billing._insert",
    "redwood_kit.billing_store.Billing._insert",
    ("table", "row"),
    (),
    ("at", "canonical"),
)
UPDATE_SIGNATURE = OpSignature(
    "Billing._update",
    "redwood_kit.billing_store.Billing._update",
    ("table", "row_id", "changes"),
    (),
    ("at", "canonical"),
)
SIGNATURES: Mapping[Op, OpSignature] = {
    Op.QUERY: QUERY_SIGNATURE,
    Op.INSERT: INSERT_SIGNATURE,
    Op.UPDATE: UPDATE_SIGNATURE,
}


def _name_list(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return f"'{names[0]}'"
    if len(names) == 2:
        return f"'{names[0]}' and '{names[1]}'"
    return f"{', '.join(repr(name) for name in names[:-1])}, and '{names[-1]}'"


def bind(signature: OpSignature, args: JsonObject, injected: JsonObject) -> JsonObject:
    for name in signature.injected:
        if name in args:
            raise TypeError(f"{signature.duplicate_name}() got multiple values for keyword argument '{name}'")
    accepted = set(signature.required) | set(signature.optional) | set(signature.injected)
    for name in args:
        if name not in accepted:
            raise TypeError(f"{signature.display_name}() got an unexpected keyword argument '{name}'")
    missing = tuple(name for name in signature.required if name not in args)
    if missing:
        noun = "argument" if len(missing) == 1 else "arguments"
        raise TypeError(
            f"{signature.display_name}() missing {len(missing)} required positional "
            f"{noun}: {_name_list(missing)}"
        )
    return {**args, **injected}


def _object(value: Json, message: str) -> JsonObject:
    if not isinstance(value, dict):
        raise BillingError(message)
    return value


def state_from_json(value: JsonObject | State) -> State:
    state: State = {}
    for table in TABLES:
        rows_value = value[table]
        if not isinstance(rows_value, dict):
            raise TypeError("validated table is not an object")
        rows: dict[str, JsonObject] = {}
        for key, row_value in rows_value.items():
            if not isinstance(key, str) or not isinstance(row_value, dict):
                raise TypeError("validated row is not an object")
            rows[key] = row_value
        state[table] = rows
    return state


def empty() -> State:
    return {table: {} for table in TABLES}


def _field_descriptor(field: FieldSpec, release: Release) -> JsonObject:
    descriptor: JsonObject = {"type": field.type.value}
    if field.enum is not None:
        descriptor["enum"] = list(field.enum)
    if field.is_list:
        descriptor["list"] = True
    if field.ref is not None:
        descriptor["ref"] = field.ref.value
    if field.nullable:
        descriptor["nullable"] = True
    if field.has_default:
        default = field.default
        if isinstance(default, NoDefault):
            raise RuntimeError("field default sentinel was marked as a default")
        descriptor["default"] = copy.deepcopy(default)
    if field.is_money and field.public_name(release) != field.name:
        descriptor["type"] = "money"
    descriptor["required"] = field.required
    return descriptor


def _public_schema(release: Release) -> JsonObject:
    result: JsonObject = {}
    for table, spec in SCHEMA.items():
        if not spec.visible_in(release):
            continue
        fields: JsonObject = {}
        for field in spec.fields:
            if field.visible_in(release):
                fields[field.public_name(release)] = _field_descriptor(field, release)
        result[table.value] = {
            "notes": spec.notes,
            "writable": spec.writable,
            "selectors": list(spec.selectors),
            "fields": fields,
        }
    return result


def _field_name(table: TableSpec, name: str, release: Release, canonical: bool) -> str:
    if canonical:
        internal: str | None = name
    else:
        internal = next(
            (field.name for field in table.fields if field.visible_in(release) and field.public_name(release) == name),
            None,
        )
    if internal is None or internal not in table.field_names:
        raise BillingError(f"unknown field {name!r} for {table.name.value}", ErrorCode.UNKNOWN_FIELD)
    return internal


def _normalize_fields(
    table: TableSpec,
    values: Json,
    release: Release,
    *,
    canonical: bool = False,
    partial: bool = False,
) -> JsonObject:
    raw = _object(values, "row, changes, and where values must be objects")
    normalized: JsonObject = {}
    for name, value in raw.items():
        internal = _field_name(table, name, release, canonical)
        if internal in normalized:
            raise BillingError(f"field {name!r} was supplied more than once")
        spec = table.field(internal)
        normalized_value: Json = value
        if spec.is_money and release is Release.R1 and not canonical:
            normalized_value = parse_money(value, name)
        normalized[internal] = copy.deepcopy(normalized_value)
    if partial:
        return normalized
    _apply_defaults(normalized, table.fields)
    _require_fields(normalized, table)
    _reject_server_fields(normalized, table)
    return normalized


def _apply_defaults(values: JsonObject, fields: tuple[FieldSpec, ...]) -> None:
    for field in fields:
        if field.name not in values and field.has_default:
            default = field.default
            if isinstance(default, NoDefault):
                raise RuntimeError("field default sentinel was marked as a default")
            values[field.name] = copy.deepcopy(default)


def _require_fields(values: JsonObject, table: TableSpec) -> None:
    missing = sorted(field.name for field in table.fields if field.required and field.name not in values)
    if missing:
        raise BillingError(f"missing fields for {table.name.value}: {', '.join(missing)}", ErrorCode.MISSING_FIELDS)


def _reject_server_fields(values: JsonObject, table: TableSpec) -> None:
    reserved = sorted(field.name for field in table.fields if field.server_assigned and field.name in values)
    if reserved:
        raise BillingError(f"server-assigned fields cannot be written: {', '.join(reserved)}")


def _table(value: Json, release: Release) -> TableSpec:
    if not isinstance(value, str):
        raise BillingError(f"unknown table {value!r}", ErrorCode.NOT_FOUND)
    try:
        table = Table(value)
    except ValueError:
        raise BillingError(f"unknown table {value!r}", ErrorCode.NOT_FOUND) from None
    spec = SCHEMA[table]
    if not spec.visible_in(release):
        raise BillingError(f"unknown table {value!r}", ErrorCode.NOT_FOUND)
    return spec


def _cursor(table: str, where: JsonObject, offset: int) -> str:
    payload = json.dumps([table, where, offset], sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _read_cursor(cursor: Json, table: str, where: JsonObject) -> int:
    if not isinstance(cursor, str):
        raise BillingError("invalid cursor")
    try:
        payload = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        saved_table, saved_where, offset = json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise BillingError("invalid cursor") from error
    if type(offset) is not int:
        raise BillingError("invalid cursor")
    if saved_table != table or saved_where != where or offset < 0:
        raise BillingError("cursor does not match this query")
    return offset


def _is_selector_value(value: Json) -> bool:
    return not isinstance(value, (dict, list))


def _has_selector(where: Json, selectors: tuple[str, ...]) -> bool:
    if not isinstance(where, dict):
        return True
    return any(name in selectors and _is_selector_value(value) for name, value in where.items())


def _matches_value(row_value: Json, where_value: Json, field: FieldSpec) -> bool:
    if field.is_list and not isinstance(where_value, list):
        if not isinstance(row_value, list):
            raise TypeError(f"{field.name} must be a list")
        return where_value in row_value
    return row_value == where_value


def _row_matches(row: JsonObject, where: JsonObject, table: TableSpec) -> bool:
    return all(
        _matches_value(row[name], value, table.field(name))
        for name, value in where.items()
    )


def _page(rows: list[JsonObject], offset: int) -> tuple[list[JsonObject], int, bool]:
    page = rows[offset:offset + PAGE_SIZE]
    next_offset = offset + len(page)
    return page, next_offset, next_offset < len(rows)


def _account_ids_for_page(state: State, table: TableSpec, rows: list[JsonObject]) -> list[str]:
    account_ids: set[str] = set()
    for row in rows:
        account_id = _optional_text(row, "account_id") if "account_id" in row else None
        if table.name is Table.INVOICE_LINES:
            invoice = state["invoices"][_text(row, "invoice_id")]
            account_id = _text(invoice, "account_id")
        if account_id is not None:
            account_ids.add(account_id)
    return sorted(account_ids)


def _present_row(table: TableSpec, row: JsonObject, release: Release) -> JsonObject:
    rendered: JsonObject = {}
    for name, value in row.items():
        if name in table.field_names and not table.field(name).visible_in(release):
            continue
        field_name = table.field(name).public_name(release) if name in table.field_names else name
        if release is Release.R1 and name.endswith("_cents"):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            rendered_value: Json = format_money(value)
        else:
            rendered_value = value
        rendered[field_name] = copy.deepcopy(rendered_value)
    return rendered


def _check_at(at: Json) -> int:
    if not isinstance(at, int) or isinstance(at, bool) or at < 0:
        raise BillingError("at must be a non-negative conversation position")
    return at


def _next_id(table: TableSpec, rows: Mapping[str, JsonObject]) -> str:
    prefix = table.id_prefix
    if prefix is None:
        raise BillingError(f"{table.name.value} has no generated identifier")
    number = 1
    while f"{prefix}-{number}" in rows:
        number += 1
    return f"{prefix}-{number}"


def _lifecycle(table: TableSpec, row: JsonObject, release: Release, canonical: bool) -> None:
    if table.name is Table.DISPUTES:
        _dispute_lifecycle(row)
    if table.name is Table.CASES:
        _case_lifecycle(row, release, canonical)


def _dispute_lifecycle(row: JsonObject) -> None:
    if _text(row, "status") != "submitted":
        return
    invoice = _optional_text(row, "invoice_id")
    reason = _optional_text(row, "reason")
    lines = _texts(row, "line_ids")
    requests = _texts(row, "request_ids")
    if invoice is None or not lines or reason is None or not requests:
        raise BillingError(
            "submitted disputes need invoice_id, non-empty line_ids, reason, and request_ids",
            ErrorCode.BUSINESS_RULE,
        )


def _case_lifecycle(row: JsonObject, release: Release, canonical: bool) -> None:
    if _text(row, "status") != "escalated":
        return
    team = _optional_text(row, "team")
    if team is None or not _text(row, "summary").strip():
        raise BillingError("escalated cases need a team and non-empty summary", ErrorCode.BUSINESS_RULE)
    priority = _optional_text(row, "priority")
    if release is Release.R1 and not canonical and priority not in {"normal", "urgent"}:
        raise BillingError("priority is required: one of normal, urgent", ErrorCode.MISSING_FIELDS)


def _check_dispute_requests(state: State, account_id: str, request_ids: list[str]) -> None:
    for request_id in request_ids:
        usage = state["usage"].get(request_id)
        if usage is None or _text(usage, "account_id") != account_id:
            raise BillingError(f"unknown request id {request_id} for this account", ErrorCode.NOT_FOUND)


def _handoff(table: TableSpec, row: JsonObject, row_id: str) -> Handoff | None:
    if table.name is Table.CASES and _text(row, "status") == "escalated":
        team = _optional_text(row, "team")
        if team is None:
            raise TypeError("escalated case team must not be null")
        return Handoff(team, row_id)
    return None


class Billing:
    def __init__(self, seed: JsonObject | State | None = None, release: str = "R0") -> None:
        try:
            self.release = Release(release)
        except ValueError:
            raise ValueError(f"unknown billing release {release!r}") from None
        seed_state = empty() if seed is None else state_from_json(seed)
        validate(seed_state)
        self.state = copy.deepcopy(seed_state)
        self._seed_ids = {table: set(rows) for table, rows in self.state.items()}
        self.log: list[ConsoleCall] = []
        self.handoff: Handoff | None = None

    def describe(self) -> JsonObject:
        return _public_schema(self.release)

    def handle(self, request: JsonObject, at: int) -> JsonObject:
        entry = self._log_entry(request, at)
        try:
            if self.handoff is not None:
                raise BillingError(
                    f"this conversation was already handed to {self.handoff.team} as {self.handoff.case_id}; "
                    "the console is closed for it",
                    ErrorCode.CLOSED,
                )
            result = self._dispatch(request, at, entry)
            entry.ok = True
            return result.value
        except TypeError as error:
            wrapped = BillingError(f"bad arguments for {entry.op}: {error}")
            entry.error = wrapped.message
            raise wrapped from None
        except (BillingError, ValueError) as error:
            wrapped = error if isinstance(error, BillingError) else BillingError(str(error))
            entry.error = wrapped.message
            raise wrapped from None
        finally:
            self.log.append(entry.to_call())

    def _log_entry(self, request: JsonObject, at: int) -> ConsoleLogEntry:
        op = request.get("op")
        table = request.get("table")
        args = {key: value for key, value in request.items() if key not in {"call", "op"}}
        return ConsoleLogEntry(at, op, table, copy.deepcopy(args))

    def _dispatch(self, request: JsonObject, at: int, entry: ConsoleLogEntry) -> ConsoleResult:
        op = request.get("op")
        args = {key: value for key, value in request.items() if key not in {"call", "op"}}
        if op == Op.DESCRIBE.value:
            if args:
                raise BillingError("describe takes no arguments")
            return ConsoleResult(self.describe())
        try:
            operation = Op(op) if isinstance(op, str) else None
        except ValueError:
            operation = None
        handlers: Mapping[Op, Callable[[JsonObject, int, ConsoleLogEntry], ConsoleResult]] = {
            Op.QUERY: self._dispatch_query,
            Op.INSERT: self._dispatch_insert,
            Op.UPDATE: self._dispatch_update,
        }
        if operation is not None and operation in handlers:
            return handlers[operation](args, at, entry)
        raise BillingError(f"unknown billing operation {op!r}")

    def _bound(self, op: Op, args: JsonObject, at: int) -> JsonObject:
        signature = SIGNATURES[op]
        injected: JsonObject = {"canonical": False}
        if op in {Op.INSERT, Op.UPDATE}:
            injected = {"at": at, "canonical": False}
        try:
            return bind(signature, args, injected)
        except TypeError as error:
            raise BillingError(f"bad arguments for {op.value}: {error}") from None

    def _dispatch_query(self, args: JsonObject, at: int, entry: ConsoleLogEntry) -> ConsoleResult:
        request = self._bound(Op.QUERY, args, at)
        table_value = request["table"]
        where_value = request.get("where")
        cursor_value = request.get("cursor")
        result, account_ids = self._query(QueryRequest(table_value, where_value, cursor_value))
        if entry.table == Table.ACCOUNTS.value and isinstance(where_value, dict) and where_value:
            entry.account_ids = account_ids
        return ConsoleResult(result, account_ids)

    def _dispatch_insert(self, args: JsonObject, at: int, entry: ConsoleLogEntry) -> ConsoleResult:
        request = self._bound(Op.INSERT, args, at)
        result = self._insert(InsertRequest(request["table"], request["row"]), request["at"], False)
        table = _table(request["table"], self.release)
        row_id = _text(result, table.id_field)
        entry.row = copy.deepcopy(self.state[table.name.value][row_id])
        return ConsoleResult(result)

    def _dispatch_update(self, args: JsonObject, at: int, entry: ConsoleLogEntry) -> ConsoleResult:
        request = self._bound(Op.UPDATE, args, at)
        result = self._update(
            UpdateRequest(request["table"], request["row_id"], request["changes"]),
            request["at"],
            False,
        )
        table = _table(request["table"], self.release)
        row_id = _text(result, table.id_field)
        entry.row = copy.deepcopy(self.state[table.name.value][row_id])
        return ConsoleResult(result)

    def _query(self, request: QueryRequest) -> tuple[JsonObject, list[str]]:
        table = _table(request.table, self.release)
        where_value = {} if request.where is None else request.where
        where = _normalize_fields(table, where_value, self.release, partial=True)
        if not _has_selector(where, table.selectors):
            selectors = (table.field(name).public_name(self.release) for name in table.selectors)
            raise BillingError(f"query on {table.name.value} needs one of: {', '.join(selectors)}")
        _check_where_values(table, where, self.state, self.release)
        offset = 0 if request.cursor is None else _read_cursor(request.cursor, table.name.value, where)
        matching = [
            self.state[table.name.value][key]
            for key in sorted(self.state[table.name.value])
            if _row_matches(self.state[table.name.value][key], where, table)
        ]
        page, next_offset, has_next = _page(matching, offset)
        next_cursor = _cursor(table.name.value, where, next_offset) if has_next else None
        account_ids = _account_ids_for_page(self.state, table, page)
        return {
            "rows": [_present_row(table, row, self.release) for row in page],
            "next_cursor": next_cursor,
        }, account_ids

    def _insert(self, request: InsertRequest, at: Json, canonical: bool) -> JsonObject:
        table = _table(request.table, self.release)
        if not table.writable:
            raise BillingError(f"{table.name.value} is read-only", ErrorCode.READ_ONLY)
        row_value = request.row
        if table.name is Table.CREDIT_NOTES and isinstance(row_value, dict):
            row_value = {key: value for key, value in row_value.items() if key != "status"} | {"status": "issued"}
        normalized = _normalize_fields(table, row_value, self.release, canonical=canonical)
        position = _check_at(at)
        assigned_id = _next_id(table, self.state[table.name.value])
        stored = {**normalized, table.id_field: assigned_id, "at": position}
        if table.name is Table.CREDIT_NOTES:
            stored["status"] = "issued"
        return self._commit(table, stored, canonical)

    def _update(self, request: UpdateRequest, at: Json, canonical: bool) -> JsonObject:
        table = _table(request.table, self.release)
        if not table.writable:
            raise BillingError(f"{table.name.value} is read-only", ErrorCode.READ_ONLY)
        if table.name is Table.CREDIT_NOTES:
            raise BillingError("credit_notes are insert-only", ErrorCode.READ_ONLY)
        row_id = _required_text(request.row_id, "row_id")
        rows = self.state[table.name.value]
        if row_id not in rows:
            raise BillingError(f"unknown {table.id_field} {row_id!r}", ErrorCode.NOT_FOUND)
        if row_id in self._seed_ids[table.name.value]:
            raise BillingError("seed history rows are immutable", ErrorCode.READ_ONLY)
        current = rows[row_id]
        if current["status"] != "draft":
            raise BillingError(f"{table.name.value} row {row_id} is not draft", ErrorCode.NOT_DRAFT)
        normalized = _normalize_fields(
            table,
            request.changes,
            self.release,
            canonical=canonical,
            partial=True,
        )
        _reject_server_fields(normalized, table)
        position = _check_at(at)
        return self._commit(table, {**current, **normalized, "at": position}, canonical)

    def _commit(self, table: TableSpec, row: JsonObject, canonical: bool) -> JsonObject:
        if table.name is Table.DISPUTES:
            _check_dispute_requests(self.state, _text(row, "account_id"), _texts(row, "request_ids"))
        _lifecycle(table, row, self.release, canonical)
        check_row(self.state, table.name, row)
        try:
            check_rules(self.state, table.name, row)
        except ValueError as error:
            raise BillingError(str(error), ErrorCode.BUSINESS_RULE) from None
        row_id = _text(row, table.id_field)
        self.state[table.name.value][row_id] = row
        self.handoff = _handoff(table, row, row_id) or self.handoff
        return _present_row(table, row, self.release)


def _required_text(value: Json, name: str) -> str:
    if not isinstance(value, str):
        raise BillingError(f"{name} must be a string")
    return value


def _check_where_values(table: TableSpec, where: JsonObject, state: State, release: Release) -> None:
    for name, value in where.items():
        spec = table.field(name)
        if spec.is_list and not isinstance(value, list):
            spec = spec.item_spec()
        check_value(value, spec, state, f"{table.name.value}.where.{name}", "query")


def _resolve_refs(value: Json, generated_ids: Mapping[int, str]) -> Json:
    if isinstance(value, str) and (match := re.fullmatch(r"\$(\d+)", value)):
        try:
            return generated_ids[int(match.group(1))]
        except KeyError as error:
            raise ValueError(f"unknown generated row reference {value!r}") from error
    if isinstance(value, list):
        return [_resolve_refs(item, generated_ids) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_refs(item, generated_ids) for key, item in value.items()}
    return value


def replay(seed: JsonObject | State | None, actions: list[JsonObject], release: str = "R0") -> State:
    billing = Billing(seed, release=release)
    action_ids: dict[int, str] = {}
    for number, action in enumerate(actions):
        at = number + 1
        op = action["op"]
        table = action["table"]
        if op == Op.INSERT.value:
            row_value = _resolve_refs(action["row"], action_ids)
            if not isinstance(row_value, dict):
                raise TypeError("replay row must be an object")
            result = billing._insert(InsertRequest(table, row_value), at, True)
            schema = _table(table, billing.release)
            action_ids[number] = _text(result, schema.id_field)
        elif op == Op.UPDATE.value:
            row_id = _resolve_refs(action["row_id"], action_ids)
            changes = _resolve_refs(action["changes"], action_ids)
            billing._update(UpdateRequest(table, row_id, changes), at, True)
        else:
            raise ValueError(f"unsupported replay operation {op!r}")
    return billing.state


PROJECTION: Mapping[str, tuple[str, ...]] = {
    "credit_notes": ("account_id", "invoice_id", "line_ids", "amount_cents", "reason"),
    "disputes": ("account_id", "invoice_id", "line_ids", "reason", "request_ids", "status"),
    "cases": ("account_id", "team", "status", "dispute_id"),
}


def _project_row(row: JsonObject, fields: tuple[str, ...]) -> JsonObject:
    item = {field: copy.deepcopy(row[field]) for field in fields}
    for field in ("line_ids", "request_ids"):
        values = item.get(field)
        if isinstance(values, list):
            values.sort()
    return item


def _dispute_key(state: State, dispute_id: Json) -> Json:
    """A case's dispute by content, so server-assigned dispute numbering never decides a match."""
    dispute = state["disputes"].get(dispute_id) if isinstance(dispute_id, str) else None
    if dispute is None:
        return dispute_id
    return _project_row(dispute, PROJECTION["disputes"])


def project(state: State) -> dict[str, list[JsonObject]]:
    projected: dict[str, list[JsonObject]] = {}
    for table, fields in PROJECTION.items():
        rows = [
            _project_row(row, fields)
            for row in state[table].values()
            if row["status"] != "draft"
        ]
        if table == "cases":
            rows = [{**row, "dispute_id": _dispute_key(state, row["dispute_id"])} for row in rows]
        projected[table] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    return projected


PRACTICE_SEED = Path(__file__).with_name("development_seed.json")
R1_PRACTICE_SEED = Path("/opt/redwood-overlays/R1/development_seed.json")
RELEASE_FILE = Path("/etc/redwood-release")


def _read_state(path: Path) -> State:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not is_json_object(value):
        raise TypeError(f"{path} must be an object")
    return state_from_json(value)


def practice_seed(release: str, r1_delta: Path = R1_PRACTICE_SEED) -> State:
    """The practice records: the R0 seed, plus the R1 overlay's delta at R1."""
    state = _read_state(PRACTICE_SEED)
    if Release(release) is Release.R1:
        for table, rows in _read_state(r1_delta).items():
            state[table].update(rows)
    return state


def _reply(store: Billing, request: object, at: int) -> JsonObject:
    if not is_json_object(request):
        return {"ok": False, "error": "request must be a JSON object"}
    try:
        return {"ok": True, **store.handle(request, at)}
    except BillingError as error:
        return {"ok": False, "error": str(error)}


def local_console(release: str, r1_delta: Path = R1_PRACTICE_SEED) -> Client:
    """An in-process practice console, as one connection to the practice service sees it."""
    store = Billing(practice_seed(release, r1_delta), release=release)
    calls = itertools.count(1)
    return Client(lambda request: reply_body(_reply(store, request, next(calls))))


class PracticeServer(socketserver.ThreadingUnixStreamServer):
    """Serves the builder's `development_billing()`: a fresh console per connection."""

    daemon_threads = True

    def __init__(self, path: str, release_file: Path, r1_delta: Path) -> None:
        self.release_file, self.r1_delta = release_file, r1_delta
        super().__init__(path, _PracticeHandler)


class _PracticeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        if not isinstance(self.server, PracticeServer):
            raise TypeError("practice handler needs a PracticeServer")
        release = self.server.release_file.read_text().strip()
        store = Billing(practice_seed(release, self.server.r1_delta), release=release)
        for at, line in enumerate(self.rfile, start=1):
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                request = None
            self.wfile.write(json.dumps(_reply(store, request, at)).encode() + b"\n")


def open_practice_server(
    path: str = PRACTICE_SOCKET,
    *,
    release_file: Path = RELEASE_FILE,
    r1_delta: Path = R1_PRACTICE_SEED,
    grant_harbor: bool = True,
) -> PracticeServer:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).unlink(missing_ok=True)
    server = PracticeServer(path, release_file, r1_delta)
    if grant_harbor:
        Path(path).parent.chmod(0o755)
        os.chown(path, 0, grp.getgrnam("harbor").gr_gid)
        os.chmod(path, 0o660)
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the root-owned practice console service.")
    parser.add_argument("--serve", action="store_true")
    if parser.parse_args().serve:
        with open_practice_server() as practice_server:
            practice_server.serve_forever()
