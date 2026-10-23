"""Schema-driven helpers over the billing console.

The console's field names and money format change between releases (R1: `*_cents` integers became
`{"value": "60.00", "currency": "USD"}` money objects, writable tables use `billing_account_id`,
escalated cases need a `priority`). Everything here reads `describe()` instead of assuming a shape,
so tools and hooks work on either format.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

_SCHEMAS: dict[int, dict] = {}


def schema(billing) -> dict:
    key = id(billing)
    if key not in _SCHEMAS:
        try:
            _SCHEMAS[key] = billing.describe()
        except Exception:  # noqa: BLE001 - fall back to field-name probing
            _SCHEMAS[key] = {}
    return _SCHEMAS[key]


def fields(billing, table: str) -> dict:
    return (schema(billing).get(table) or {}).get("fields") or {}


def account_key(billing, table: str) -> str:
    f = fields(billing, table)
    return "billing_account_id" if "billing_account_id" in f else "account_id"


def query_all(billing, table: str, where: dict, limit_pages: int = 40) -> list[dict]:
    rows, cursor = [], None
    for _ in range(limit_pages):
        page = billing.query(table, where, cursor=cursor) if cursor else billing.query(table, where)
        rows.extend(page.get("rows", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows


def by_account(billing, table: str, account_id: str) -> list[dict]:
    return query_all(billing, table, {account_key(billing, table): account_id})


def row_account(row: dict) -> str:
    return row.get("billing_account_id") or row.get("account_id") or ""


# ---------------------------------------------------------------- money
def to_cents(value) -> int | None:
    """Cents from an int (old `*_cents`), a money object, or a decimal string."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("not money")
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        value = value.get("value")
    try:
        d = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"not a money amount: {value!r}") from None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_money(row: dict, base: str) -> int | None:
    """Cents of money field `base` (e.g. "amount", "total", "remaining") in either format."""
    if base in row:
        return to_cents(row[base])
    if f"{base}_cents" in row:
        return to_cents(row[f"{base}_cents"])
    return None


def usd(cents: int | None) -> str:
    if cents is None:
        return "n/a"
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def money_field(billing, table: str, base: str, cents: int) -> tuple[str, object]:
    """(field name, value) for writing `cents` into `table` in the live format."""
    f = fields(billing, table)
    if base in f:
        if f[base].get("type") == "money":
            return base, {"value": f"{Decimal(cents) / 100:.2f}", "currency": "USD"}
        return base, cents
    if f"{base}_cents" in f:
        return f"{base}_cents", cents
    return base, {"value": f"{Decimal(cents) / 100:.2f}", "currency": "USD"}


def display(row: dict) -> dict:
    """A row with money objects/cents also shown as "$1,234.00" strings for quoting."""
    out = dict(row)
    for k, v in row.items():
        if isinstance(v, dict) and set(v) >= {"value", "currency"}:
            out[k] = usd(to_cents(v))
        elif k.endswith("_cents") and isinstance(v, int):
            out[k.removesuffix("_cents")] = usd(v)
    return out


# ---------------------------------------------------------------- time
def local_time(utc_text: str, tz_name: str | None) -> str:
    """An ISO UTC timestamp shown in the account's billing timezone."""
    if not utc_text:
        return ""
    try:
        ts = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        tz = ZoneInfo(tz_name) if (ZoneInfo and tz_name) else timezone.utc
        return ts.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001
        return ""


def today() -> date:
    return datetime.now(timezone.utc).date()


def parse_date(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- derived facts
def duplicate_analysis(billing, lines: list[dict]) -> list[dict]:
    """For lines on one invoice that bill the same request ids: which is the original, which the copy.

    Each usage record names the one line that bills it; the line the records point to is the
    original, the other is the copy. Records pointing to neither or both lines leave it unconfirmed.
    """
    groups: dict[tuple, list[str]] = {}
    for line in lines:
        ids = tuple(sorted(line.get("request_ids") or []))
        if ids:
            groups.setdefault(ids, []).append(line["line_id"])
    out = []
    for ids, members in groups.items():
        if len(members) < 2:
            continue
        pointed: set[str] = set()
        missing: list[str] = []
        for rid in ids:
            recs = query_all(billing, "usage", {"request_id": rid})
            if not recs:
                missing.append(rid)
            for r in recs:
                if r.get("line_id"):
                    pointed.add(r["line_id"])
        inside = [m for m in members if m in pointed]
        if len(inside) == 1 and not missing:
            original = inside[0]
            copies = [m for m in members if m != original]
            out.append({"lines": members, "request_ids": list(ids), "original_line": original,
                        "copy_lines": copies, "console_confirms_duplicate": True})
        else:
            out.append({"lines": members, "request_ids": list(ids), "original_line": None, "copy_lines": [],
                        "console_confirms_duplicate": False,
                        "why": "usage records point to neither or both lines" if not missing
                        else f"no usage record for {missing}"})
    return out


def credit_balances(billing, account_id: str) -> dict:
    """Per-grant balances. Uses the credit ledger when the console has one (sum of entries per grant);
    otherwise the legacy credit_grants remaining amounts."""
    now = today()
    if "credit_ledger" in schema(billing):
        entries = by_account(billing, "credit_ledger", account_id)
        grants: dict[str, dict] = {}
        for e in sorted(entries, key=lambda e: (str(e.get("on")), str(e.get("entry_id")))):
            g = grants.setdefault(e.get("grant_id"), {"grant_id": e.get("grant_id"), "kind": e.get("credit_kind"),
                                                      "balance_cents": 0, "expires_on": None, "entries": []})
            g["balance_cents"] += read_money(e, "amount") or 0
            if e.get("expires_on"):
                g["expires_on"] = e["expires_on"]
            g["entries"].append({"on": e.get("on"), "entry": e.get("entry"), "amount": usd(read_money(e, "amount")),
                                 "invoice_id": e.get("invoice_id")})
        active_total = 0
        out = []
        for g in grants.values():
            exp = parse_date(g["expires_on"])
            expired = bool(exp and exp < now)
            if not expired:
                active_total += g["balance_cents"]
            out.append({"grant_id": g["grant_id"], "kind": g["kind"], "balance": usd(g["balance_cents"]),
                        "expires_on": g["expires_on"] or "does not expire", "expired": expired,
                        "entries": g["entries"]})
        return {"source": "credit_ledger (sum of entries per grant)", "grants": out,
                "total_available": usd(active_total),
                "note": "credit_grants is not shown: the ledger is the console's source of truth for balances."}
    grants = by_account(billing, "credit_grants", account_id)
    total = 0
    out = []
    for g in grants:
        rem = read_money(g, "remaining") or 0
        exp = parse_date(g.get("expires_on"))
        expired = bool(exp and exp < now)
        if not expired:
            total += rem
        out.append({"grant_id": g.get("grant_id"), "kind": g.get("kind"), "balance": usd(rem),
                    "granted": usd(read_money(g, "amount")), "expires_on": g.get("expires_on") or "does not expire",
                    "expired": expired})
    return {"source": "credit_grants", "grants": out, "total_available": usd(total)}
