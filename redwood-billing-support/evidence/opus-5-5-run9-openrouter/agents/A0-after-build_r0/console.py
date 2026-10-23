"""Billing-console read helpers shared by tools.py and hooks.py.

The runtime hands the console to the toolkit and to create_agent, not to hooks, so agent.py
registers it here with `attach()`; hooks read it through `billing()`.
"""
from __future__ import annotations

MAX_PAGES = 8
_BILLING = None


def attach(billing) -> None:
    global _BILLING
    _BILLING = billing


def billing():
    return _BILLING


def rows(console, table: str, where: dict) -> list[dict]:
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        page = console.query(table, where, cursor)
        out.extend(page.get("rows", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return out


def credited_by_line(console, invoice_id: str) -> tuple[dict[str, int], list[dict]]:
    notes = rows(console, "credit_notes", {"invoice_id": invoice_id})
    credited: dict[str, int] = {}
    for note in notes:
        for line_id in note.get("line_ids", []):
            credited[line_id] = credited.get(line_id, 0) + int(note.get("amount_cents", 0))
    return credited, notes


def duplicate_groups(lines: list[dict]) -> list[list[dict]]:
    """Lines on one invoice that bill exactly the same (non-empty) set of request ids."""
    groups: dict[tuple, list[dict]] = {}
    for line in lines:
        key = tuple(sorted(line.get("request_ids") or []))
        if key:
            groups.setdefault(key, []).append(line)
    return [group for group in groups.values() if len(group) > 1]
