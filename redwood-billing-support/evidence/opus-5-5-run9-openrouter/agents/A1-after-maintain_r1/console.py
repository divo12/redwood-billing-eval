"""Billing-console helpers shared by tools.py and hooks.py.

The console's record format changes between releases (R1: money objects instead of *_cents
integers, `billing_account_id` on writable tables, a required priority on escalated cases, a credit
ledger). Everything here reads the live `describe()` so the same code writes the shape the console
expects. The runtime hands the console to the toolkit and to create_agent, not to hooks, so
agent.py registers it here with `attach()`; hooks read it through `billing()`.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MAX_PAGES = 8
_BILLING = None
_SCHEMA: dict | None = None


def attach(billing) -> None:
    global _BILLING, _SCHEMA
    _BILLING, _SCHEMA = billing, None


def billing():
    return _BILLING


def schema(console=None) -> dict:
    global _SCHEMA
    console = console or _BILLING
    if _SCHEMA is None and console is not None:
        try:
            _SCHEMA = console.describe()
        except Exception:  # describe is safe to repeat; fall back to the last known shape
            return {}
    return _SCHEMA or {}


def fields(table: str, console=None) -> dict:
    return (schema(console).get(table) or {}).get("fields") or {}


def has_table(table: str, console=None) -> bool:
    return table in schema(console)


def account_field(table: str, console=None) -> str:
    """The field that names the account on `table` (R1 writable tables use billing_account_id)."""
    f = fields(table, console)
    if "billing_account_id" in f:
        return "billing_account_id"
    return "account_id"


def money_field(table: str, base: str, console=None) -> str:
    """`amount` (money object) or `amount_cents` (integer), whichever the live schema has."""
    f = fields(table, console)
    if base in f:
        return base
    if f"{base}_cents" in f:
        return f"{base}_cents"
    return base


# ------------------------------------------------------------------ money


def cents_of(value) -> int | None:
    """Integer cents from a console money value: {"value": "60.00", ...}, an int of cents, or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("value")
        if raw is None:
            return None
        return int((Decimal(str(raw)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return None


def row_cents(row: dict, base: str) -> int | None:
    """Cents of money field `base` on a row in either format (amount / amount_cents)."""
    if f"{base}_cents" in row:
        return cents_of(row[f"{base}_cents"])
    return cents_of(row.get(base))


def money_value(table: str, base: str, cents: int, console=None) -> tuple[str, object]:
    """(field name, value) to write `cents` into money field `base` of `table`."""
    name = money_field(table, base, console)
    if name.endswith("_cents"):
        return name, int(cents)
    return name, {"value": f"{Decimal(int(cents)) / 100:.2f}", "currency": "USD"}


def dollars(cents: int | None) -> str:
    if cents is None:
        return "n/a"
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


# ------------------------------------------------------------------ reads


def rows(console, table: str, where: dict) -> list[dict]:
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        page = console.query(table, where, cursor)
        out.extend(page.get("rows", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return out


def rows_for_account(console, table: str, account_id: str) -> list[dict]:
    return rows(console, table, {account_field(table, console): account_id})


def credited_by_line(console, invoice_id: str) -> tuple[dict[str, int], list[dict]]:
    notes = rows(console, "credit_notes", {"invoice_id": invoice_id})
    credited: dict[str, int] = {}
    for note in notes:
        for line_id in note.get("line_ids", []):
            credited[line_id] = credited.get(line_id, 0) + int(row_cents(note, "amount") or 0)
    return credited, notes


def duplicate_groups(lines: list[dict]) -> list[list[dict]]:
    """Lines on one invoice that bill exactly the same (non-empty) set of request ids."""
    groups: dict[tuple, list[dict]] = {}
    for line in lines:
        key = tuple(sorted(line.get("request_ids") or []))
        if key:
            groups.setdefault(key, []).append(line)
    return [group for group in groups.values() if len(group) > 1]


def classify_duplicate(console, account_id: str, group: list[dict]) -> dict:
    """Which line of a same-request-ids group is the original and which the copy.

    Each request's usage record names the one line that bills it: that line is the original, the
    other line(s) are copies. Records pointing to none or to several lines of the group leave the
    duplicate unconfirmed.
    """
    line_ids = [line["line_id"] for line in group]
    pointed: set[str] = set()
    records = []
    for rid in group[0].get("request_ids") or []:
        for rec in rows(console, "usage", {"request_id": rid}):
            if rec.get("account_id") in (None, account_id):
                records.append({"request_id": rid, "line_id": rec.get("line_id")})
                if rec.get("line_id") in line_ids:
                    pointed.add(rec["line_id"])
    if len(pointed) == 1:
        original = next(iter(pointed))
        return {"confirmed": True, "original": original,
                "copies": [l for l in line_ids if l != original], "usage_points_to": records}
    return {"confirmed": False, "original": None, "copies": [], "usage_points_to": records,
            "why": "usage records point to " + ("none" if not pointed else "more than one") +
                   " of these lines, so the console does not confirm which is the copy"}
