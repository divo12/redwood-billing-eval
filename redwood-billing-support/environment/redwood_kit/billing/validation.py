"""Validate billing records: JSON shape and references, then the rules a console write can break.

Invariants of the read-only tables are data facts, checked once by the repository tests.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn

from ..jsontypes import Json, JsonObject
from .schema import SCHEMA, TABLES, FieldSpec, FieldType, Rows, State, Table, TableSpec
from .schema import integer as _integer
from .schema import optional_text as _optional_text
from .schema import text as _text
from .schema import texts as _texts

type RowRule = Callable[[State, str, JsonObject, Path | str], None]


def _fail(path: str, problem: str, source: Path | str) -> NoReturn:
    raise ValueError(f"{source}:{path}: {problem}")


def _is_date(value: Json) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_utc(value: Json) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() == timedelta(0)
    except ValueError:
        return False


def _is_text(value: Json) -> bool:
    return isinstance(value, str)


def _is_integer(value: Json) -> bool:
    return type(value) is int


def _is_boolean(value: Json) -> bool:
    return type(value) is bool


def _is_string_map(value: Json) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    )


_VALUE_CHECKS: Mapping[FieldType, Callable[[Json], bool]] = {
    FieldType.STRING: _is_text,
    FieldType.INTEGER: _is_integer,
    FieldType.BOOLEAN: _is_boolean,
    FieldType.DATE: _is_date,
    FieldType.UTC: _is_utc,
    FieldType.STRING_MAP: _is_string_map,
}


def check_value(value: Json, spec: FieldSpec, state: State, path: str, source: Path | str) -> None:
    if value is None and spec.nullable:
        return
    if spec.is_list:
        _check_list(value, spec, state, path, source)
        return
    validator = _VALUE_CHECKS[spec.type]
    if not validator(value):
        _fail(path, f"must have type {spec.type.value}", source)
    _check_enum(value, spec, path, source)
    _check_reference(value, spec, state, path, source)


def _check_list(value: Json, spec: FieldSpec, state: State, path: str, source: Path | str) -> None:
    if not isinstance(value, list):
        _fail(path, "must be a list", source)
    item_spec = spec.item_spec()
    for index, item in enumerate(value):
        check_value(item, item_spec, state, f"{path}[{index}]", source)


def _check_enum(value: Json, spec: FieldSpec, path: str, source: Path | str) -> None:
    if spec.enum is not None and value not in spec.enum:
        _fail(path, f"must be one of {', '.join(spec.enum)}", source)


def _check_reference(value: Json, spec: FieldSpec, state: State, path: str, source: Path | str) -> None:
    if spec.ref is not None and value not in state[spec.ref.value]:
        _fail(path, f"unknown {spec.ref.value} id {value}", source)


def _shape_state(value: Json | State, source: Path | str) -> State:
    if not isinstance(value, dict) or set(value) != set(TABLES):
        _fail("$", f"expected exactly these tables: {', '.join(TABLES)}", source)
    state: State = {}
    for table in TABLES:
        rows = value[table]
        if not isinstance(rows, dict):
            _fail(table, "must be an object keyed by record id", source)
        state[table] = _shape_rows(table, rows, source)
    return state


def _shape_rows(table: str, rows: Mapping[str, Json], source: Path | str) -> Rows:
    schema = SCHEMA[Table(table)]
    typed_rows: Rows = {}
    for key, value in rows.items():
        path = f"{table}.{key}"
        if not isinstance(key, str) or not isinstance(value, dict):
            _fail(path, "must be an object keyed by record id", source)
        row = value
        if row.get(schema.id_field) != key:
            _fail(f"{path}.{schema.id_field}", "must match its table key", source)
        _check_field_set(row, schema, path, source)
        typed_rows[key] = row
    return typed_rows


def _check_field_set(row: JsonObject, schema: TableSpec, path: str, source: Path | str) -> None:
    expected = set(schema.field_names)
    if set(row) != expected:
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        _fail(path, f"fields differ; missing={missing}, extra={extra}", source)


def _validate_shape_and_values(state: State, source: Path | str) -> None:
    for table in TABLES:
        spec = SCHEMA[Table(table)]
        for key, row in state[table].items():
            _check_row_fields(row, spec, f"{table}.{key}", state, source)


def _check_row_fields(row: JsonObject, schema: TableSpec, path: str, state: State, source: Path | str) -> None:
    for field in schema.fields:
        check_value(row[field.name], field, state, f"{path}.{field.name}", source)


def _check_credit_note(state: State, key: str, row: JsonObject, source: Path | str) -> None:
    path = f"credit_notes.{key}"
    invoice_id = _text(row, "invoice_id")
    invoice = state["invoices"][invoice_id]
    if _text(invoice, "account_id") != _text(row, "account_id"):
        _fail(f"{path}.invoice_id", "belongs to another account", source)
    line_ids = _texts(row, "line_ids")
    if not line_ids or len(line_ids) != len(set(line_ids)):
        _fail(f"{path}.line_ids", "must be a non-empty list of distinct lines", source)
    if _integer(row, "amount_cents") <= 0:
        _fail(f"{path}.amount_cents", "must be positive", source)
    for line_id in line_ids:
        if _text(state["invoice_lines"][line_id], "invoice_id") != invoice_id:
            _fail(f"{path}.line_ids", "lines must belong to the invoice", source)


def _check_credit_balances(state: State, source: Path | str) -> None:
    _allocate_notes(state, list(state["credit_notes"].values()), source)


def _allocate_notes(state: State, notes: list[JsonObject], source: Path | str) -> None:
    remaining = {key: _integer(row, "amount_cents") for key, row in state["invoice_lines"].items()}
    for note in sorted(notes, key=lambda row: (_integer(row, "at"), _text(row, "credit_note_id"))):
        _allocate_note(note, remaining, source)


def _allocate_note(note: JsonObject, remaining: dict[str, int], source: Path | str) -> None:
    note_id = _text(note, "credit_note_id")
    line_ids = _texts(note, "line_ids")
    amount = _integer(note, "amount_cents")
    available = sum(remaining[line_id] for line_id in line_ids)
    if amount > available:
        _fail(f"credit_notes.{note_id}.amount_cents", "exceeds uncredited line balance", source)
    for line_id in sorted(line_ids):
        allocation = min(remaining[line_id], amount)
        remaining[line_id] -= allocation
        amount -= allocation
        if not amount:
            break


def _check_dispute(state: State, key: str, row: JsonObject, source: Path | str) -> None:
    path = f"disputes.{key}"
    invoice_id = _optional_text(row, "invoice_id")
    if invoice_id is not None and _text(state["invoices"][invoice_id], "account_id") != _text(row, "account_id"):
        _fail(f"{path}.invoice_id", "belongs to another account", source)
    line_ids = _texts(row, "line_ids")
    if line_ids and invoice_id is None:
        _fail(f"{path}.line_ids", "requires an invoice", source)
    if len(line_ids) != len(set(line_ids)):
        _fail(f"{path}.line_ids", "must contain distinct lines", source)
    _check_dispute_lines(state, line_ids, invoice_id, path, source)
    _check_dispute_requests(state, row, path, source)
    _check_submitted_dispute(row, invoice_id, line_ids, path, source)


def _check_submitted_dispute(
    row: JsonObject,
    invoice_id: str | None,
    line_ids: list[str],
    path: str,
    source: Path | str,
) -> None:
    if _text(row, "status") != "submitted":
        return
    if invoice_id is None or not line_ids:
        _fail(path, "submitted disputes need invoice, lines, reason, and request_ids", source)
    if _optional_text(row, "reason") is None or not _texts(row, "request_ids"):
        _fail(path, "submitted disputes need invoice, lines, reason, and request_ids", source)


def _check_dispute_lines(
    state: State,
    line_ids: list[str],
    invoice_id: str | None,
    path: str,
    source: Path | str,
) -> None:
    for line_id in line_ids:
        if _text(state["invoice_lines"][line_id], "invoice_id") != invoice_id:
            _fail(f"{path}.line_ids", "lines must belong to the invoice", source)


def _check_dispute_requests(state: State, row: JsonObject, path: str, source: Path | str) -> None:
    for request_id in _texts(row, "request_ids"):
        if _text(state["usage"][request_id], "account_id") != _text(row, "account_id"):
            _fail(f"{path}.request_ids", "must reference usage for this account", source)



def _check_case(state: State, key: str, row: JsonObject, source: Path | str) -> None:
    path = f"cases.{key}"
    dispute_id = _optional_text(row, "dispute_id")
    if dispute_id is not None and _text(state["disputes"][dispute_id], "account_id") != _text(row, "account_id"):
        _fail(f"{path}.dispute_id", "belongs to another account", source)
    if _text(row, "status") == "escalated":
        team = _optional_text(row, "team")
        if team is None or not _text(row, "summary").strip():
            _fail(path, "escalated cases need a team and summary", source)


_ROW_RULES: Mapping[Table, RowRule] = {
    Table.CREDIT_NOTES: _check_credit_note,
    Table.DISPUTES: _check_dispute,
    Table.CASES: _check_case,
}


def validate(state: Json | State, source: Path | str = "state") -> None:
    """Check a whole state: shape, values and references, then every writable row."""
    typed_state = _shape_state(state, source)
    _validate_shape_and_values(typed_state, source)
    for table, rule in _ROW_RULES.items():
        for key, row in typed_state[table.value].items():
            rule(typed_state, key, row, source)
    _check_credit_balances(typed_state, source)


def check_row(state: State, table: Table, row: JsonObject, source: Path | str = "state") -> None:
    """Check one row's fields, values and references before it is written."""
    schema = SCHEMA[table]
    _check_row_fields(row, schema, f"{table.value}.{_text(row, schema.id_field)}", state, source)


def check_rules(state: State, table: Table, row: JsonObject, source: Path | str = "state") -> None:
    """Check the business rules one written row must satisfy against the committed state."""
    _ROW_RULES[table](state, _text(row, SCHEMA[table].id_field), row, source)
    if table is Table.CREDIT_NOTES:
        _allocate_notes(state, [*state["credit_notes"].values(), row], source)
