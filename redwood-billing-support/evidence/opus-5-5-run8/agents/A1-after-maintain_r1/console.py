"""Schema-adaptive helpers over the billing console, shared by tools and hooks.

The console's field names change between releases (e.g. `amount_cents` -> `amount` money objects,
`account_id` -> `billing_account_id` on writable tables). Field names are read from `describe()`
so the same code works on either shape. Money is handled internally as integer cents.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import retrieval

MAX_PAGES = 8


def cents(value) -> int | None:
    """Integer cents from an int-cents field or a {"value": "12.34", "currency": "USD"} money object."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
        return int((Decimal(str(value)) * 100).to_integral_value()) if value is not None else None
    if isinstance(value, (int, float)):
        return int(value)
    return int((Decimal(str(value)) * 100).to_integral_value())


def dollars(c: int | None) -> str:
    return "" if c is None else f"${Decimal(c) / 100:,.2f}"


class Console:
    def __init__(self, billing):
        self.billing = billing
        self._schema = None

    # ------------------------------------------------------------- schema
    def schema(self) -> dict:
        if self._schema is None:
            try:
                described = self.billing.describe()
                self._schema = described.get("tables", described) if isinstance(described, dict) else {}
            except Exception:  # noqa: BLE001
                self._schema = {}
        return self._schema

    def fields(self, table: str) -> dict:
        t = self.schema().get(table) or {}
        return t.get("fields", {}) if isinstance(t, dict) else {}

    def account_field(self, table: str) -> str:
        f = self.fields(table)
        return "billing_account_id" if "billing_account_id" in f else "account_id"

    def money_field(self, table: str, base: str = "amount") -> str:
        f = self.fields(table)
        if base in f:
            return base
        if f"{base}_cents" in f:
            return f"{base}_cents"
        return base

    def money_value(self, table: str, field: str, c: int):
        spec = self.fields(table).get(field, {})
        if field.endswith("_cents") or spec.get("type") == "integer":
            return int(c)
        return {"value": f"{Decimal(c) / 100:.2f}", "currency": "USD"}

    # ------------------------------------------------------------- reads
    def rows(self, table: str, where: dict) -> list[dict]:
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            page = self.billing.query(table, where, cursor)
            out.extend(page.get("rows", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return out

    def by_account(self, table: str, account_id: str) -> list[dict]:
        return self.rows(table, {self.account_field(table): account_id})

    @staticmethod
    def row_cents(row: dict, base: str = "amount") -> int | None:
        if base in row:
            return cents(row[base])
        return cents(row.get(f"{base}_cents"))

    def account(self, account_id: str) -> dict | None:
        rows = self.rows("accounts", {"account_id": account_id})
        return rows[0] if rows else None

    def duplicate_analysis(self, invoice_id: str) -> dict:
        """Lines billing identical request ids; the original is the line the usage records point to."""
        lines = self.rows("invoice_lines", {"invoice_id": invoice_id})
        groups: dict[tuple, list[dict]] = {}
        for line in lines:
            if line.get("request_ids"):
                groups.setdefault(tuple(sorted(line["request_ids"])), []).append(line)
        out = []
        for req, members in groups.items():
            if len(members) < 2:
                continue
            pointed = set()
            for rid in req:
                for u in self.rows("usage", {"request_id": rid}):
                    if u.get("line_id"):
                        pointed.add(u["line_id"])
            ids = [m["line_id"] for m in members]
            originals = [i for i in ids if i in pointed]
            copies = [i for i in ids if i not in pointed]
            confirmed = len(originals) == 1 and len(copies) >= 1
            out.append({
                "line_ids": ids, "request_ids": list(req),
                "original_line": originals[0] if confirmed else None,
                "duplicated_lines_to_credit_or_dispute": copies if confirmed else [],
                "console_confirms_duplicate": confirmed,
                "note": ("Usage records point to the original; act only on the copy." if confirmed else
                         "Usage records point to neither or both lines: the console does not confirm a duplicate."),
            })
        return {"lines": lines, "groups": out}

    def ledger_balances(self, account_id: str, today: date | None = None) -> dict | None:
        if "credit_ledger" not in self.schema():
            return None
        today = today or datetime.now().date()
        entries = self.by_account("credit_ledger", account_id)
        grants: dict[str, dict] = {}
        for e in entries:
            g = grants.setdefault(e.get("grant_id"), {"grant_id": e.get("grant_id"), "kind": e.get("credit_kind"),
                                                      "balance_cents": 0, "expires_on": e.get("expires_on"),
                                                      "entries": []})
            g["balance_cents"] += self.row_cents(e) or 0
            if e.get("expires_on"):
                g["expires_on"] = e["expires_on"]
            g["entries"].append({k: e.get(k) for k in ("entry", "on", "invoice_id")} | {"amount": dollars(self.row_cents(e))})
        active, total = [], 0
        for g in grants.values():
            expired = bool(g["expires_on"]) and str(g["expires_on"]) < today.isoformat()
            g["expired"] = expired or any(x["entry"] in ("expired", "expiry") for x in g["entries"])
            g["balance"] = dollars(g["balance_cents"])
            if not g["expired"]:
                total += g["balance_cents"]
                active.append(g)
        return {"total_balance": dollars(total), "grants": list(grants.values()),
                "note": "Balances from credit_ledger (sum of each grant's entries). A grant with no expires_on "
                        "doesn't expire. Give each grant's balance and expiry date."}


def duplicate_limit_cents(invoice: dict) -> tuple[int | None, str]:
    """Support's per-line duplicate-credit limit for this invoice, read from the indexed approval matrix."""
    rules = retrieval.duplicate_limit_rules()
    if not rules:
        return None, "limit not found in the approval matrix"
    period = str(invoice.get("period_start") or "")
    for book_year, effective, limit in rules["by_book"]:
        if effective and period >= effective:
            return limit, f"invoice period starts {period}: rated on the {book_year} price book (from {effective})"
    if rules["earlier"] is not None:
        return rules["earlier"], f"invoice period starts {period}: rated on an earlier price book"
    if rules["flat"] is not None:
        return rules["flat"], "single per-line limit"
    limits = [lim for _, _, lim in rules["by_book"]]
    return (min(limits) if limits else None), "price book effective date not found; using the lowest limit"
