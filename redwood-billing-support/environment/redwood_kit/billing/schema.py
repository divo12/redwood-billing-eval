"""Typed table and field definitions shared by billing validation and the console."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum, StrEnum, auto
from typing import NotRequired, TypedDict, Unpack

from ..jsontypes import Json, JsonObject


class Release(StrEnum):
    R0 = "R0"
    R1 = "R1"


class Table(StrEnum):
    ACCOUNTS = "accounts"
    CONTACTS = "contacts"
    CONTRACTS = "contracts"
    INVOICES = "invoices"
    INVOICE_LINES = "invoice_lines"
    USAGE = "usage"
    CREDIT_GRANTS = "credit_grants"
    CREDIT_LEDGER = "credit_ledger"
    CREDIT_NOTES = "credit_notes"
    DISPUTES = "disputes"
    CASES = "cases"


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    UTC = "utc"
    STRING_MAP = "map<string,string>"


class NoDefault(Enum):
    VALUE = auto()


NO_DEFAULT = NoDefault.VALUE


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: FieldType
    enum: tuple[str, ...] | None = None
    ref: Table | None = None
    is_list: bool = False
    nullable: bool = False
    default: Json | NoDefault = NO_DEFAULT
    server_assigned: bool = False
    release: Release = Release.R0
    r1_name: str | None = None  # the field's name on the R1 console, when R1 renamed it

    def visible_in(self, release: Release) -> bool:
        return self.release is Release.R0 or release is Release.R1

    @property
    def has_default(self) -> bool:
        return self.default is not NO_DEFAULT

    @property
    def is_money(self) -> bool:
        return self.name.endswith("_cents")

    @property
    def required(self) -> bool:
        return not self.server_assigned and not self.has_default

    def public_name(self, release: Release) -> str:
        if release is Release.R1 and self.r1_name is not None:
            return self.r1_name
        if release is Release.R1 and self.is_money:
            return self.name[:-6]
        return self.name

    def item_spec(self) -> FieldSpec:
        return replace(self, is_list=False)


@dataclass(frozen=True)
class TableSpec:
    name: Table
    notes: str
    writable: bool
    selectors: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    id_field: str
    release: Release = Release.R0
    id_prefix: str | None = None

    def field(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields)

    def visible_in(self, release: Release) -> bool:
        return self.release is Release.R0 or release is Release.R1


class _FieldOptions(TypedDict, total=False):
    enum: tuple[str, ...] | None
    ref: Table | None
    is_list: bool
    nullable: bool
    default: Json | NoDefault
    server_assigned: bool
    release: Release
    r1_name: str | None


class _TableOptions(TypedDict):
    id_field: str
    release: NotRequired[Release]
    id_prefix: NotRequired[str | None]


def _f(
    name: str,
    kind: FieldType,
    **options: Unpack[_FieldOptions],
) -> FieldSpec:
    return FieldSpec(name, kind, **options)


def _table(
    name: Table,
    notes: str,
    writable: bool,
    selectors: tuple[str, ...],
    fields: tuple[FieldSpec, ...],
    **options: Unpack[_TableOptions],
) -> TableSpec:
    return TableSpec(name, notes, writable, selectors, fields, **options)


# R1 renames the account field on the writable tables (record-format-changes-2027); reads and writes use it.
BILLING_ACCOUNT = "billing_account_id"

SCHEMA: Mapping[Table, TableSpec] = {
    Table.ACCOUNTS: _table(Table.ACCOUNTS, "Account identity and billing configuration.", False,
        ("account_id", "org"), (
            _f("account_id", FieldType.STRING), _f("org", FieldType.STRING),
            _f("plan", FieldType.STRING, enum=("free", "self_serve", "enterprise")),
            _f("plan_name", FieldType.STRING),
            _f("billing_channel", FieldType.STRING, enum=("direct", "aws_marketplace", "gcp_marketplace")),
            _f("status", FieldType.STRING, enum=("active", "suspended")),
            _f("payment_method", FieldType.STRING, enum=("card", "invoice", "marketplace", "none")),
            _f("created_on", FieldType.DATE),
            _f("billing_timezone", FieldType.STRING, release=Release.R1),
        ), id_field="account_id"),
    Table.CONTACTS: _table(Table.CONTACTS, "Account contact records.", False,
        ("contact_id", "account_id", "email"), (
            _f("contact_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("name", FieldType.STRING), _f("email", FieldType.STRING),
            _f("role", FieldType.STRING, enum=("owner", "billing_admin", "developer")),
        ), id_field="contact_id"),
    Table.CONTRACTS: _table(Table.CONTRACTS, "Signed account terms and service dates.", False,
        ("contract_id", "account_id"), (
            _f("contract_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("sla", FieldType.BOOLEAN), _f("signed_terms", FieldType.STRING, is_list=True),
            _f("start_on", FieldType.DATE), _f("end_on", FieldType.DATE, nullable=True),
        ), id_field="contract_id"),
    Table.INVOICES: _table(Table.INVOICES, "Invoice header records.", False,
        ("invoice_id", "account_id"), (
            _f("invoice_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("period_start", FieldType.DATE), _f("period_end", FieldType.DATE),
            _f("issued_on", FieldType.DATE), _f("status", FieldType.STRING, enum=("open", "paid")),
            _f("total_cents", FieldType.INTEGER),
        ), id_field="invoice_id"),
    Table.INVOICE_LINES: _table(Table.INVOICE_LINES, "Invoice line details and linked requests.", False,
        ("line_id", "invoice_id"), (
            _f("line_id", FieldType.STRING), _f("invoice_id", FieldType.STRING, ref=Table.INVOICES),
            _f("description", FieldType.STRING),
            _f("product", FieldType.STRING, enum=("hosted_api", "embeddings", "batch", "dedicated", "plan_fee", "tax")),
            _f("period_start", FieldType.DATE), _f("period_end", FieldType.DATE),
            _f("amount_cents", FieldType.INTEGER),
            _f("request_ids", FieldType.STRING, is_list=True, ref=Table.USAGE),
        ), id_field="line_id"),
    Table.USAGE: _table(Table.USAGE, "Usage request records.", False,
        ("request_id", "account_id", "line_id"), (
            _f("request_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("at", FieldType.UTC), _f("http_status", FieldType.INTEGER),
            _f("billed_tokens", FieldType.INTEGER), _f("cached_tokens", FieldType.INTEGER),
            _f("line_id", FieldType.STRING, ref=Table.INVOICE_LINES, nullable=True),
        ), id_field="request_id"),
    Table.CREDIT_GRANTS: _table(Table.CREDIT_GRANTS, "Granted account credits.", False,
        ("grant_id", "account_id"), (
            _f("grant_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("kind", FieldType.STRING, enum=("promotional", "service", "prepaid")),
            _f("amount_cents", FieldType.INTEGER), _f("remaining_cents", FieldType.INTEGER),
            _f("granted_on", FieldType.DATE), _f("expires_on", FieldType.DATE, nullable=True),
        ), id_field="grant_id"),
    Table.CREDIT_LEDGER: _table(Table.CREDIT_LEDGER,
        "Credit balance entries (source of truth for credit balances from 1 April 2027).", False,
        ("entry_id", "account_id", "grant_id"), (
            _f("entry_id", FieldType.STRING), _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS),
            _f("grant_id", FieldType.STRING),
            _f("credit_kind", FieldType.STRING, enum=("promotional", "service", "prepaid")),
            _f("entry", FieldType.STRING, enum=("granted", "applied", "expired", "reinstated")),
            _f("amount_cents", FieldType.INTEGER), _f("on", FieldType.DATE),
            _f("expires_on", FieldType.DATE, nullable=True),
            _f("invoice_id", FieldType.STRING, ref=Table.INVOICES, nullable=True),
        ), id_field="entry_id", release=Release.R1),
    Table.CREDIT_NOTES: _table(Table.CREDIT_NOTES, "Issued credit note records.", True,
        ("credit_note_id", "account_id", "invoice_id"), (
            _f("credit_note_id", FieldType.STRING, server_assigned=True),
            _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS, r1_name=BILLING_ACCOUNT),
            _f("invoice_id", FieldType.STRING, ref=Table.INVOICES),
            _f("line_ids", FieldType.STRING, is_list=True, ref=Table.INVOICE_LINES),
            _f("amount_cents", FieldType.INTEGER),
            _f("reason", FieldType.STRING, enum=("duplicate", "service_credit", "goodwill")),
            _f("memo", FieldType.STRING, default=""),
            _f("status", FieldType.STRING, enum=("issued",), default="issued"),
            _f("at", FieldType.INTEGER, server_assigned=True),
        ), id_field="credit_note_id", id_prefix="CN"),
    Table.DISPUTES: _table(Table.DISPUTES, "Billing dispute records.", True,
        ("dispute_id", "account_id"), (
            _f("dispute_id", FieldType.STRING, server_assigned=True),
            _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS, r1_name=BILLING_ACCOUNT),
            _f("invoice_id", FieldType.STRING, ref=Table.INVOICES, nullable=True, default=None),
            _f("line_ids", FieldType.STRING, is_list=True, ref=Table.INVOICE_LINES, default=[]),
            _f("reason", FieldType.STRING, enum=("duplicate_charge", "incorrect_amount", "unrecognized_usage"),
               nullable=True, default=None),
            _f("request_ids", FieldType.STRING, is_list=True, ref=Table.USAGE, default=[]),
            _f("window", FieldType.STRING, default=""),
            _f("status", FieldType.STRING, enum=("draft", "submitted")),
            _f("at", FieldType.INTEGER, server_assigned=True),
        ), id_field="dispute_id", id_prefix="DSP"),
    Table.CASES: _table(Table.CASES, "Support case records.", True,
        ("case_id", "account_id"), (
            _f("case_id", FieldType.STRING, server_assigned=True),
            _f("account_id", FieldType.STRING, ref=Table.ACCOUNTS, r1_name=BILLING_ACCOUNT),
            _f("team", FieldType.STRING, enum=("billing_ops", "billing_engineering", "finance", "legal",
                "account_team", "partnerships", "telemetry", "sre", "platform"), nullable=True, default=None),
            _f("summary", FieldType.STRING), _f("details", FieldType.STRING_MAP, default={}),
            _f("priority", FieldType.STRING, enum=("normal", "urgent"), nullable=True, default=None,
               release=Release.R1),
            _f("dispute_id", FieldType.STRING, ref=Table.DISPUTES, nullable=True, default=None),
            _f("status", FieldType.STRING, enum=("draft", "escalated")),
            _f("at", FieldType.INTEGER, server_assigned=True),
        ), id_field="case_id", id_prefix="CASE"),
}

TABLES = tuple(table.value for table in SCHEMA)
ID_FIELDS: Mapping[str, str] = {table.value: spec.id_field for table, spec in SCHEMA.items()}
PAGE_SIZE = 25


# ---- rows: typed state aliases and field accessors

type Rows = dict[str, JsonObject]
type State = dict[str, Rows]


def text(row: JsonObject, name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    return value


def optional_text(row: JsonObject, name: str) -> str | None:
    value = row[name]
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be text or null")
    return value


def integer(row: JsonObject, name: str) -> int:
    value = row[name]
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    return value


def texts(row: JsonObject, name: str) -> list[str]:
    value = row[name]
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list of text")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{name} must be a list of text")
        result.append(item)
    return result
