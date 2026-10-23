"""Actions the billing support agent can take: knowledge search, console reads, console writes.

Console writes are final (no undo). Hooks in hooks.py guard them; the tools themselves validate
their inputs because the runtime does not. Field names and money formats are read from the
console's describe() so a record-format change (e.g. April 2027: money objects, billing_account_id
on writable tables) doesn't break the tools.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools, load_subagents
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

from ledger import LEDGER

LEDGER_ROOT = Path(__file__).resolve().parent
MAX_PAGES = 8
CASE_TEAMS = ("billing_ops", "billing_engineering", "finance", "legal", "account_team", "partnerships",
              "telemetry", "sre", "platform")


def _text(value: object, name: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()[:limit]


def _ids(value: object, name: str) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.replace(";", ",").split(",")]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of strings")
    return [v.strip() for v in value if v.strip()][:50]


def cents(value: object) -> int:
    """Cents from a console money object {"value": "340.00"}, a legacy *_cents int, or a number/string in USD."""
    if isinstance(value, dict):
        value = value.get("value", "0")
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int((Decimal(str(value).replace("$", "").replace(",", "").strip()) * 100)
                   .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError(f"not an amount: {value!r}") from None


def usd(amount_cents: int) -> str:
    return f"{Decimal(amount_cents) / 100:.2f}"


def row_cents(row: dict, base: str) -> int:
    """Amount of a row's money field `base` (amount, total, remaining) in either format."""
    if base in row:
        return cents(row[base])
    return cents(row.get(f"{base}_cents", 0))


class Tools(DeveloperTools, SkillTools, SubagentTools, ToolKitBase):
    _retriever = None

    def __init__(self) -> None:
        LEDGER.toolkit = self
        self._described: dict | None = None

    # ------------------------------------------------------------------ console format

    def _schema(self) -> dict:
        if self._described is None:
            try:
                self._described = self.billing.describe()
            except Exception:  # describe unavailable: fall back to the April 2027 format
                self._described = {}
        return self._described

    def _fields(self, table: str) -> dict:
        info = self._schema().get(table) or {}
        return info.get("fields") or {}

    def has_table(self, table: str) -> bool:
        return not self._schema() or table in self._schema()

    def account_field(self, table: str) -> str:
        fields = self._fields(table)
        if "billing_account_id" in fields or not fields:
            return "billing_account_id" if table in ("credit_notes", "disputes", "cases") else "account_id"
        return "account_id"

    def money(self, table: str, base: str, amount_cents: int) -> dict:
        """Field name and value for a money write in the table's current format."""
        fields = self._fields(table)
        if f"{base}_cents" in fields and base not in fields:
            return {f"{base}_cents": amount_cents}
        return {base: {"value": usd(amount_cents), "currency": "USD"}}

    # ------------------------------------------------------------------ helpers

    def _all(self, table: str, where: dict) -> list[dict]:
        rows: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            page = self.billing.query(table, where, cursor)
            rows.extend(page.get("rows", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows

    def _by_account(self, table: str, account_id: str) -> list[dict]:
        if not self.has_table(table):
            return []
        try:
            return self._all(table, {self.account_field(table): account_id})
        except BillingError as error:
            return [{"error": str(error)}]

    def _account(self, account_id: str | None, org: str | None) -> dict | None:
        for key, value in (("account_id", account_id), ("org", org), ("account_id", org), ("org", account_id)):
            if isinstance(value, str) and value.strip():
                try:
                    rows = self.billing.query("accounts", {key: value.strip()}).get("rows", [])
                except BillingError:
                    rows = []
                if rows:
                    return rows[0]
        return None

    def _line_credits(self, invoice_id: str) -> dict[str, int]:
        credited: dict[str, int] = {}
        for note in self._all("credit_notes", {"invoice_id": invoice_id}):
            lines = note.get("line_ids") or []
            for line in lines:
                credited[line] = credited.get(line, 0) + row_cents(note, "amount") // max(1, len(lines))
        return credited

    def credit_balances(self, account_id: str) -> dict:
        """Per-grant balances from the credit ledger (sum of entries); expired grants count as zero."""
        today = dt.date.today().isoformat()
        if self.has_table("credit_ledger"):
            entries = self._by_account("credit_ledger", account_id)
            grants: dict[str, dict] = {}
            for entry in entries:
                if "error" in entry:
                    continue
                grant = grants.setdefault(entry.get("grant_id") or "?", {
                    "grant_id": entry.get("grant_id"), "kind": entry.get("credit_kind"), "balance_cents": 0,
                    "expires_on": entry.get("expires_on"), "entries": []})
                grant["balance_cents"] += row_cents(entry, "amount")
                grant["expires_on"] = entry.get("expires_on") or grant["expires_on"]
                grant["entries"].append({k: entry.get(k) for k in ("entry", "amount", "on", "invoice_id")})
            out, total = [], 0
            for grant in grants.values():
                expired = bool(grant["expires_on"]) and grant["expires_on"] < today
                balance = 0 if expired else grant["balance_cents"]
                total += max(0, balance)
                out.append({"grant_id": grant["grant_id"], "kind": grant["kind"], "balance": usd(balance),
                            "expires_on": grant["expires_on"] or "does not expire", "expired": expired,
                            "entries": grant["entries"]})
            return {"source": "credit_ledger (sum of entries per grant; credit_grants is not quoted)",
                    "total_balance": usd(total), "grants": out}
        grants = self._by_account("credit_grants", account_id)
        total = sum(row_cents(g, "remaining") for g in grants if "error" not in g
                    and not (g.get("expires_on") and g["expires_on"] < today))
        return {"source": "credit_grants", "total_balance": usd(total), "grants": grants}

    # ------------------------------------------------------------------ delegation

    @is_tool(ToolType.GENERIC)
    def delegate(self, subagent: str, task: str) -> dict[str, str]:
        """Hand a self-contained task to a specialist subagent and get its answer.

        Args:
            subagent: Name of a subagent listed in your instructions.
            task: Everything the subagent needs; it sees nothing else of the conversation.
        """
        specs = load_subagents(LEDGER_ROOT)
        if subagent not in specs:
            raise ValueError(f"unknown subagent {subagent!r}; available: {sorted(specs)}")
        LEDGER.delegating = specs[subagent].tools
        try:
            return SubagentTools.delegate(self, subagent, task)
        finally:
            LEDGER.delegating = None

    # ------------------------------------------------------------------ knowledge

    @is_tool(ToolType.READ)
    def search_knowledge(self, query: str, top_k: int = 6) -> dict:
        """Search Redwood's internal knowledge (policies, procedures, tickets, threads, meeting notes) for passages.

        Returns chunks with source id, title, status and last-updated date. Check status and date:
        drafts, proposals and archived pages are not current policy. Search again with different
        words (exact terms, page titles, team names) if the first results do not contain the rule.

        Args:
            query: What you need to know, in plain words; include distinctive terms or identifiers.
            top_k: Number of passages to return (1-10).
        """
        query = _text(query, "query", 1000)
        top_k = max(1, min(10, int(top_k or 6)))
        if Tools._retriever is None:
            from retrieval import Retriever
            Tools._retriever = Retriever(self.gateway)
        results = Tools._retriever.search(query, top_k)
        LEDGER.note_results(results)
        return {"query": query, "results": results}

    # ------------------------------------------------------------------ console reads

    @is_tool(ToolType.READ)
    def account_overview(self, account_id: str = "", org: str = "") -> dict:
        """Look up a billing account by account id or org name: plan, billing channel, billing timezone,
        contracts, credit balances (from the credit ledger), invoices, credit notes, disputes and cases.

        Args:
            account_id: The account id, if known.
            org: The org name or id the developer gave, if no account id.
        """
        account = self._account(account_id, org)
        if account is None:
            return {"found": False, "searched": {"account_id": account_id, "org": org},
                    "note": "No account matches. Ask the developer for their org or account id."}
        aid = account["account_id"]
        contracts = self._by_account("contracts", aid)
        return {
            "found": True,
            "account": account,
            "has_signed_contract": any("error" not in c for c in contracts),
            "contacts": [{k: c.get(k) for k in ("name", "role")} for c in self._by_account("contacts", aid)],
            "contracts": contracts,
            "credits": self.credit_balances(aid),
            "invoices": self._by_account("invoices", aid),
            "credit_notes": self._by_account("credit_notes", aid),
            "disputes": self._by_account("disputes", aid),
            "cases": self._by_account("cases", aid),
        }

    @is_tool(ToolType.READ)
    def invoice_details(self, invoice_id: str) -> dict:
        """Read one invoice with its lines, credits already issued per line, and the console's duplicate check.

        `console_confirmed_duplicates` lists groups of lines on this invoice that bill the same request
        ids, with the original (the line the usage records point to) and the copy (the line no usage
        record points to). `lookalike_lines` lists lines with the same description and amount but
        different request ids (not duplicates by the console).

        Args:
            invoice_id: The invoice number.
        """
        invoice_id = _text(invoice_id, "invoice_id")
        invoices = self.billing.query("invoices", {"invoice_id": invoice_id}).get("rows", [])
        if not invoices:
            return {"found": False, "invoice_id": invoice_id}
        invoice = invoices[0]
        lines = self._all("invoice_lines", {"invoice_id": invoice_id})
        credited = self._line_credits(invoice_id)
        for line in lines:
            amount = row_cents(line, "amount")
            line["credited"] = usd(credited.get(line["line_id"], 0))
            line["uncredited"] = usd(amount - credited.get(line["line_id"], 0))
        groups: dict[tuple, list[dict]] = {}
        for line in lines:
            key = tuple(sorted(line.get("request_ids") or []))
            if key:
                groups.setdefault(key, []).append(line)
        duplicates = []
        for key, group in groups.items():
            if len(group) < 2:
                continue
            pointed = set()
            for request_id in key:
                for usage in self.billing.query("usage", {"request_id": request_id}).get("rows", []):
                    if usage.get("line_id"):
                        pointed.add(usage["line_id"])
            ids = [l["line_id"] for l in group]
            originals = [i for i in ids if i in pointed]
            copies = [i for i in ids if i not in pointed]
            entry = {"line_ids": ids, "shared_request_ids": list(key),
                     "amounts": [usd(row_cents(l, "amount")) for l in group]}
            if len(originals) == 1 and copies:
                entry.update({"original_line": originals[0], "copy_lines": copies,
                              "note": "Credit or dispute only the copy line(s); leave the original alone."})
            else:
                entry.update({"original_line": None, "copy_lines": [],
                              "note": "Usage records point to neither or several of these lines: the console does "
                                      "not confirm which is the copy (treat as an unconfirmed duplicate)."})
            duplicates.append(entry)
        looks: dict[tuple, list[dict]] = {}
        for line in lines:
            looks.setdefault((line.get("description"), row_cents(line, "amount")), []).append(line)
        lookalikes = [{"line_ids": [l["line_id"] for l in group],
                       "request_ids": {l["line_id"]: l.get("request_ids") for l in group}}
                      for group in looks.values()
                      if len(group) > 1 and len({tuple(sorted(l.get("request_ids") or [])) for l in group}) > 1]
        account = self._account(invoice.get("account_id"), None) or {}
        contracts = self._by_account("contracts", account.get("account_id", "")) if account else []
        return {
            "found": True,
            "invoice": invoice,
            "account": {k: account.get(k) for k in ("account_id", "org", "plan", "plan_name", "billing_channel",
                                                    "payment_method", "status", "billing_timezone")},
            "has_signed_contract": any("error" not in c for c in contracts),
            "lines": lines,
            "console_confirmed_duplicates": duplicates,
            "lookalike_lines": lookalikes,
            "credit_notes": self._all("credit_notes", {"invoice_id": invoice_id}),
        }

    @is_tool(ToolType.READ)
    def usage_lookup(self, request_ids: list[str]) -> dict:
        """Look up usage records by request id: time (UTC, and in the account's billing timezone), HTTP status,
        billed and cached tokens, and the invoice line that bills each one (null if not on a Redwood invoice line).

        Args:
            request_ids: Request ids the developer gave.
        """
        found, missing = [], []
        zones: dict[str, str] = {}
        for request_id in _ids(request_ids, "request_ids")[:20]:
            rows = self.billing.query("usage", {"request_id": request_id}).get("rows", [])
            if not rows:
                missing.append(request_id)
                continue
            row = rows[0]
            if row.get("line_id"):
                line = self.billing.query("invoice_lines", {"line_id": row["line_id"]}).get("rows", [])
                row["invoice_id"] = line[0]["invoice_id"] if line else None
            aid = row.get("account_id", "")
            if aid not in zones:
                zones[aid] = (self._account(aid, None) or {}).get("billing_timezone") or "UTC"
            row["billing_timezone"] = zones[aid]
            try:
                from zoneinfo import ZoneInfo
                at = dt.datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00"))
                row["at_billing_timezone"] = at.astimezone(ZoneInfo(zones[aid])).isoformat()
            except Exception:
                pass
            found.append(row)
        return {"usage": found, "not_found": missing}

    @is_tool(ToolType.READ)
    def console_query(self, table: str, filters: dict[str, str]) -> dict:
        """Read rows from any billing console table (see console_schema for tables and selectors).

        Args:
            table: Table name, e.g. accounts, invoices, invoice_lines, usage, credit_ledger, contracts,
                credit_notes, disputes, cases.
            filters: Field values to match; must include at least one selector of the table.
        """
        table = _text(table, "table", 50)
        if not isinstance(filters, dict) or not filters:
            raise ValueError("filters must be an object with at least one selector")
        filters = dict(filters)
        if "account_id" in filters and self.account_field(table) != "account_id":
            filters[self.account_field(table)] = filters.pop("account_id")
        return {"rows": self._all(table, filters)[:100]}

    @is_tool(ToolType.READ)
    def console_schema(self) -> dict:
        """Describe the billing console: tables, fields, enums, selectors and which tables are writable."""
        return self.billing.describe()

    # ------------------------------------------------------------------ console writes

    @is_tool(ToolType.WRITE)
    def issue_credit_note(
        self, account_id: str, invoice_id: str, line_ids: list[str], amount: str, reason: str,
        memo: str, policy_source: str, policy_limit: str,
    ) -> dict:
        """Issue a credit note against an invoice line. Final; never retry after an error without checking
        invoice_details first. Requires the developer's explicit yes via confirm_with_developer.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice the line is on.
            line_ids: The one line being credited (the duplicate's copy line).
            amount: Credit amount in USD as a decimal string, e.g. "60.00".
            reason: duplicate, service_credit or goodwill.
            memo: Short note: what is credited and why (evidence).
            policy_source: Source id of the retrieved policy passage that sets Support's limit for this invoice.
            policy_limit: The limit that applies to this invoice, exactly as that passage states it, e.g. "$X per duplicated line".
        """
        row = {
            self.account_field("credit_notes"): _text(account_id, "account_id"),
            "invoice_id": _text(invoice_id, "invoice_id"),
            "line_ids": _ids(line_ids, "line_ids"),
            **self.money("credit_notes", "amount", cents(amount)),
            "reason": _text(reason, "reason", 40),
            "memo": (memo or "")[:500],
        }
        note = self.billing.insert("credit_notes", row)
        LEDGER.writes.append({"credit_note": note})
        return {"issued": note}

    @is_tool(ToolType.WRITE)
    def open_dispute(
        self, account_id: str, invoice_id: str, line_ids: list[str], reason: str, request_ids: list[str],
        window: str = "", submit: bool = True,
    ) -> dict:
        """Open a billing dispute on invoice lines (submitted by default; submitted disputes are final).
        Requires the developer's explicit yes via confirm_with_developer.

        Args:
            account_id: The account id from the console.
            invoice_id: The disputed invoice.
            line_ids: The disputed line(s) (for a duplicate, the copy line).
            reason: duplicate_charge, incorrect_amount or unrecognized_usage.
            request_ids: Request ids the disputed lines bill (from the console).
            window: Time window of the disputed usage, in the account's billing timezone.
            submit: True to submit now; False to leave a draft.
        """
        row = {
            self.account_field("disputes"): _text(account_id, "account_id"),
            "invoice_id": _text(invoice_id, "invoice_id"),
            "line_ids": _ids(line_ids, "line_ids"),
            "reason": _text(reason, "reason", 40),
            "request_ids": _ids(request_ids, "request_ids"),
            "window": (window or "")[:200],
            "status": "submitted" if submit else "draft",
        }
        dispute = self.billing.insert("disputes", row)
        LEDGER.writes.append({"dispute": dispute})
        return {"dispute": dispute}

    @is_tool(ToolType.WRITE)
    def escalate_case(
        self, account_id: str, team: str, summary: str, details: dict[str, str] | None = None,
        dispute_id: str = "", priority: str = "normal",
    ) -> dict:
        """Hand the case to a Redwood team. This ends your access to the console for this conversation,
        so answer everything you can and make all other writes first.

        Args:
            account_id: The account id from the console.
            team: billing_ops, billing_engineering, finance, legal, account_team, partnerships, telemetry,
                sre or platform.
            summary: What the developer asked for and what you found, in a few sentences.
            details: Intake facts as name -> value (invoice_id, line_ids, request_ids, amounts, window in the
                account's billing timezone, error_code, plan/contract, missing details, policy sources...).
            dispute_id: The dispute id if you opened one.
            priority: normal (the standard for billing handoffs) or urgent (only where the routing policy says so).
        """
        team = _text(team, "team", 40)
        if team not in CASE_TEAMS:
            raise ValueError(f"team must be one of {CASE_TEAMS}")
        priority = (priority or "normal").strip().lower()
        if priority not in ("normal", "urgent"):
            raise ValueError("priority must be normal or urgent")
        clean = {str(k)[:60]: str(v)[:500] for k, v in (details or {}).items()} if isinstance(details, dict) else {}
        account_id = _text(account_id, "account_id")
        # Link the dispute opened in this conversation even if the model put its id only in details.
        dispute = (dispute_id.strip() if isinstance(dispute_id, str) else "") or clean.get("dispute_id", "").strip()
        if not dispute:
            opened = [w["dispute"] for w in LEDGER.writes if "dispute" in w
                      and account_id in (w["dispute"].get("account_id"), w["dispute"].get("billing_account_id"))]
            dispute = opened[-1].get("dispute_id", "") if opened else ""
        row = {
            self.account_field("cases"): account_id,
            "team": team,
            "summary": _text(summary, "summary", 2000),
            "details": clean,
            "dispute_id": dispute or None,
            "status": "escalated",
        }
        if "priority" in self._fields("cases") or not self._fields("cases"):
            row["priority"] = priority
        case = self.billing.insert("cases", row)
        LEDGER.escalated = True
        LEDGER.writes.append({"case": case})
        return {"case": {k: case.get(k) for k in ("case_id", "team", "status", "priority", "dispute_id")}}
