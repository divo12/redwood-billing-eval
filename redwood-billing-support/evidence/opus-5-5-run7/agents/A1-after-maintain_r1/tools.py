"""Actions the billing support agent can take.

Reads: knowledge search over the shipped index, and billing-console lookups that return the facts
Support needs (account, contracts, credit ledger, invoices with duplicate analysis, usage).
Writes: credit note, dispute, escalated case. Writes are never retried automatically; a write that
errors must be checked with a read before anything else is attempted (see hooks.py).

The console's record format changes between releases (money objects, `billing_account_id` on writable
tables, case priority). Field names are taken from the live `describe()` so reads and writes follow it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

from retrieval import Retriever

MAX_PAGES = 8


def cents_of(value) -> int:
    """Money from either console format: int cents, or {"value": "12.34", "currency": "USD"}."""
    if isinstance(value, dict):
        value = value.get("value")
        return int((Decimal(str(value)) * 100).to_integral_value(ROUND_HALF_UP))
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def money_text(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def money_of(row: dict, *names: str) -> int:
    for name in names:
        if name in row and row[name] is not None:
            return cents_of(row[name])
    return 0


class Tools(DeveloperTools, SkillTools, SubagentTools, ToolKitBase):
    _retriever: Retriever | None = None
    _schema: dict | None = None

    # ------------------------------------------------------------------ helpers

    def _search_index(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(getattr(self, "gateway", None))
        return self._retriever

    def _console(self):
        if self.billing is None:
            raise RuntimeError("billing console is not connected")
        return self.billing

    def _describe(self) -> dict:
        if self._schema is None:
            self._schema = self._console().describe()
        return self._schema

    def _fields(self, table: str) -> dict:
        try:
            return self._describe()[table]["fields"]
        except (KeyError, TypeError, BillingError):
            return {}

    def _has_table(self, table: str) -> bool:
        try:
            return table in self._describe()
        except BillingError:
            return False

    def _acct(self, table: str) -> str:
        """The account field name on this table (writable tables use billing_account_id since April 2027)."""
        fields = self._fields(table)
        return "billing_account_id" if "billing_account_id" in fields else "account_id"

    def _money_field(self, table: str, new: str, old: str, cents: int) -> dict:
        fields = self._fields(table)
        if new in fields and fields[new].get("type") == "money":
            return {new: {"value": f"{Decimal(cents) / 100:.2f}", "currency": "USD"}}
        if old in fields:
            return {old: cents}
        return {new: {"value": f"{Decimal(cents) / 100:.2f}", "currency": "USD"}}

    def _all(self, table: str, where: dict) -> list[dict]:
        if "account_id" in where and table in ("credit_notes", "disputes", "cases"):
            where = {(self._acct(table) if k == "account_id" else k): v for k, v in where.items()}
        rows, cursor = [], None
        for _ in range(MAX_PAGES):
            page = self._console().query(table, where, cursor)
            rows.extend(page.get("rows", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows

    def _enum(self, table: str, field: str) -> list[str]:
        return list(self._fields(table).get(field, {}).get("enum") or [])

    @staticmethod
    def _text(value, name: str, limit: int = 200) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} must be a non-empty string of at most {limit} characters")
        return value.strip()

    def _resolve_account(self, account_id: str | None, org: str | None) -> dict | None:
        if account_id:
            rows = self._all("accounts", {"account_id": self._text(account_id, "account_id")})
            if rows:
                return rows[0]
        if org:
            rows = self._all("accounts", {"org": self._text(org, "org")})
            if rows:
                return rows[0]
        return None

    def _ledger_summary(self, account_id: str) -> dict:
        entries = self._all("credit_ledger", {"account_id": account_id})
        today = date.today().isoformat()
        grants: dict[str, dict] = {}
        for e in sorted(entries, key=lambda x: (str(x.get("on")), str(x.get("entry_id")))):
            g = grants.setdefault(e["grant_id"], {"grant_id": e["grant_id"], "credit_kind": e.get("credit_kind"),
                                                   "balance_cents": 0, "expires_on": e.get("expires_on"),
                                                   "entries": []})
            amount = money_of(e, "amount", "amount_cents")
            g["balance_cents"] += amount
            g["expires_on"] = e.get("expires_on") or g["expires_on"]
            g["entries"].append({"entry": e.get("entry"), "amount": money_text(amount), "on": e.get("on"),
                                 "invoice_id": e.get("invoice_id")})
        total = 0
        for g in grants.values():
            expired = any(x["entry"] in ("expired", "expiry") for x in g["entries"]) or bool(
                g["expires_on"] and str(g["expires_on"]) < today)
            g["expired"] = expired
            g["balance"] = money_text(g["balance_cents"])
            if not expired:
                total += g["balance_cents"]
        return {"source": "credit_ledger", "as_of": today, "grants": list(grants.values()),
                "total_unexpired_balance": money_text(total)}

    # ------------------------------------------------------------------ knowledge

    @is_tool(ToolType.READ)
    def search_knowledge(self, query: str, include_archive: bool = False) -> dict:
        """Search Redwood's internal knowledge (policy pages, Finance documents, support procedures, status notices, past tickets, Slack/email) for passages relevant to a billing question.

        Each result has source, kind, title, status (published/final/draft/in_review/ARCHIVED...) and date. Prefer current published policy; drafts, proposals, archived pages and chat threads are not current rules. Run several focused searches for multi-part questions.

        Args:
            query: A focused question or key terms, e.g. "who approves goodwill credit" or "duplicate charge credit limit per line".
            include_archive: Also search the non-billing archive (engineering, CRM, transcripts) lexically; use for exact ticket ids or rare terms.
        """
        query = self._text(query, "query", 500)
        results = self._search_index().search(query, k=7, include_archive=bool(include_archive))
        return {"query": query, "results": results}

    # ------------------------------------------------------------------ console reads

    @is_tool(ToolType.READ)
    def lookup_account(self, account_id: str = "", org: str = "") -> dict:
        """Look up a customer's billing account by account id or org name: plan, channel, billing timezone, contracts, invoices, credit balances (from the credit ledger), and existing credit notes, disputes and cases.

        Args:
            account_id: The account id, if known.
            org: The org name the customer gave (e.g. "acme-labs"), if no account id.
        """
        if not account_id and not org:
            raise ValueError("give account_id or org; ask the developer for their org_id if you have neither")
        account = self._resolve_account(account_id or None, org or None)
        if account is None:
            return {"found": False, "searched": {"account_id": account_id, "org": org},
                    "next_step": "Ask the developer to confirm their org id or account id."}
        aid = account["account_id"]
        out: dict = {"found": True, "account": account}
        for table in ("contracts", "invoices", "credit_notes", "disputes", "cases"):
            try:
                out[table] = self._all(table, {"account_id": aid})
            except BillingError as error:
                out[table] = f"unavailable: {error}"
        out["has_signed_contract"] = bool(isinstance(out.get("contracts"), list) and out["contracts"])
        if isinstance(out.get("invoices"), list):
            for inv in out["invoices"]:
                inv["total_text"] = money_text(money_of(inv, "total", "total_cents"))
        try:
            if self._has_table("credit_ledger"):
                out["credits"] = self._ledger_summary(aid)
                out["credits_note"] = ("Balances are computed from credit_ledger entries. credit_grants is not "
                                       "shown; check the current credits policy before using it.")
            else:
                grants = self._all("credit_grants", {"account_id": aid})
                out["credits"] = {"source": "credit_grants", "grants": grants, "total_remaining": money_text(
                    sum(money_of(g, "remaining", "remaining_cents") for g in grants))}
        except BillingError as error:
            out["credits"] = f"unavailable: {error}"
        return out

    @is_tool(ToolType.READ)
    def get_invoice(self, invoice_id: str) -> dict:
        """Get one invoice with its lines, the account's plan/channel/timezone, credit notes already issued against it, and a duplicate analysis: lines billing identical request ids, with the copy identified from the usage records.

        Args:
            invoice_id: The invoice id exactly as in the console.
        """
        invoice_id = self._text(invoice_id, "invoice_id")
        headers = self._all("invoices", {"invoice_id": invoice_id})
        if not headers:
            return {"found": False, "invoice_id": invoice_id,
                    "next_step": "Check the id with the developer or list the account's invoices with lookup_account."}
        invoice = headers[0]
        account = self._resolve_account(invoice.get("account_id"), None) or {}
        contracts = self._all("contracts", {"account_id": invoice["account_id"]}) if account else []
        lines = self._all("invoice_lines", {"invoice_id": invoice_id})
        notes = self._all("credit_notes", {"invoice_id": invoice_id})
        credited: dict[str, int] = {}
        for note in notes:
            for lid in note.get("line_ids") or []:
                credited[lid] = credited.get(lid, 0) + money_of(note, "amount", "amount_cents")
        for line in lines:
            cents = money_of(line, "amount", "amount_cents")
            line["amount_text"] = money_text(cents)
            line["credited"] = money_text(credited.get(line["line_id"], 0))
            line["uncredited"] = money_text(cents - credited.get(line["line_id"], 0))
        groups: dict[tuple, list[dict]] = {}
        for line in lines:
            key = tuple(sorted(line.get("request_ids") or []))
            if key:
                groups.setdefault(key, []).append(line)
        confirmed, unconfirmed = [], []
        for key, members in groups.items():
            if len(members) < 2:
                continue
            usage = []
            for rid in key:
                usage.extend(self._all("usage", {"request_id": rid}))
            ids = {m["line_id"] for m in members}
            pointed = {u.get("line_id") for u in usage if u.get("line_id") in ids}
            entry = {"request_ids": list(key), "line_ids": sorted(ids),
                     "usage_records_point_to": sorted(pointed),
                     "amounts": {m["line_id"]: m["amount_text"] for m in members}}
            if len(pointed) == 1:
                original = next(iter(pointed))
                entry.update(original_line=original, copy_lines=sorted(ids - {original}))
                confirmed.append(entry)
            else:
                entry["why_unconfirmed"] = "usage records point to neither line or to more than one"
                unconfirmed.append(entry)
        same_amount: dict[tuple, list[str]] = {}
        for line in lines:
            same_amount.setdefault((line["amount_text"], line.get("description")), []).append(line["line_id"])
        in_groups = {lid for g in confirmed + unconfirmed for lid in g["line_ids"]}
        lookalikes = [ids for ids in same_amount.values() if len(ids) > 1 and not set(ids) <= in_groups]
        invoice["total_text"] = money_text(money_of(invoice, "total", "total_cents"))
        return {
            "found": True,
            "invoice": invoice,
            "account": {k: account.get(k) for k in ("account_id", "org", "plan", "plan_name", "billing_channel",
                                                    "payment_method", "billing_timezone")},
            "has_signed_contract": bool(contracts),
            "lines": lines,
            "credit_notes": notes,
            "console_confirmed_duplicates": confirmed,
            "same_requests_but_unconfirmed": unconfirmed,
            "same_amount_but_different_requests": lookalikes,
            "note": ("Confirmed duplicate = two lines on this invoice billing the same request ids, where the usage "
                     "records point to exactly one of them (the original); the other is the copy. Act only on the "
                     "copy. Same amount/description with different request ids is NOT a confirmed duplicate."),
        }

    @is_tool(ToolType.READ)
    def get_usage(self, request_ids: list[str]) -> dict:
        """Look up usage records for request ids: account, time (UTC), HTTP status, billed and cached tokens, and the invoice line that billed them (null = not billed on a Redwood invoice line).

        Args:
            request_ids: One or more request ids the developer gave.
        """
        if not isinstance(request_ids, list) or not request_ids or len(request_ids) > 25:
            raise ValueError("request_ids must be a list of 1 to 25 ids")
        found, missing = [], []
        for rid in request_ids:
            rows = self._all("usage", {"request_id": self._text(rid, "request_id")})
            (found.extend(rows) if rows else missing.append(rid))
        return {"usage": found, "not_found": missing}

    @is_tool(ToolType.READ)
    def console_query(self, table: str, where: dict, cursor: str = "") -> dict:
        """Read any billing-console table directly (for lookups the other tools don't cover). Needs at least one selector field of that table (see console_schema).

        Args:
            table: Table name, e.g. "usage", "invoice_lines", "contacts", "credit_ledger".
            where: Field filters, e.g. {"line_id": "INV-1/L2"}.
            cursor: Continuation cursor from a previous page, if any.
        """
        table = self._text(table, "table", 60)
        where = dict(where or {})
        if "account_id" in where and "account_id" not in self._fields(table) and "billing_account_id" in self._fields(table):
            where["billing_account_id"] = where.pop("account_id")
        return self._console().query(table, where or None, cursor or None)

    @is_tool(ToolType.READ)
    def console_schema(self) -> dict:
        """Show the billing console's current tables, fields, selectors, enums and required fields."""
        return self._describe()

    @is_tool(ToolType.READ)
    def date_calculator(self, from_date: str, to_date: str = "", add_business_days: int = 0, add_days: int = 0) -> dict:
        """Date arithmetic for billing questions: weekdays, the gap between two dates in calendar days and in business days (Mon-Fri), and a date plus N business or calendar days. Use it instead of counting by hand (e.g. retry schedules from past incident notices, thresholds, invoice periods).

        Args:
            from_date: A date as YYYY-MM-DD.
            to_date: Optional second date as YYYY-MM-DD to measure the gap to.
            add_business_days: Optional number of business days (Mon-Fri) to add to from_date.
            add_days: Optional number of calendar days to add to from_date.
        """
        start = date.fromisoformat(self._text(from_date, "from_date", 10))
        out: dict = {"from_date": start.isoformat(), "from_weekday": start.strftime("%A")}
        if to_date:
            end = date.fromisoformat(self._text(to_date, "to_date", 10))
            step = 1 if end >= start else -1
            business, day = 0, start
            while day != end:
                day += timedelta(days=step)
                if day.weekday() < 5:
                    business += step
            out.update(to_date=end.isoformat(), to_weekday=end.strftime("%A"),
                       calendar_days=(end - start).days, business_days=business)
        if add_business_days:
            day, left = start, int(add_business_days)
            while left > 0:
                day += timedelta(days=1)
                if day.weekday() < 5:
                    left -= 1
            out.update(plus_business_days=int(add_business_days), result=day.isoformat(), result_weekday=day.strftime("%A"))
        if add_days:
            day = start + timedelta(days=int(add_days))
            out.update(plus_days=int(add_days), calendar_result=day.isoformat(), calendar_result_weekday=day.strftime("%A"))
        return out

    # ------------------------------------------------------------------ console writes

    def _parse_amount(self, amount) -> int:
        try:
            value = Decimal(str(amount).replace("$", "").replace(",", "").strip())
        except InvalidOperation:
            raise ValueError("amount must be a dollar amount such as \"180.00\"") from None
        cents = int((value * 100).to_integral_value(ROUND_HALF_UP))
        if cents <= 0:
            raise ValueError("amount must be positive")
        return cents

    @is_tool(ToolType.WRITE)
    def issue_credit_note(self, account_id: str, invoice_id: str, line_id: str, amount: str,
                          reason: str, memo: str = "", extra_fields: dict | None = None) -> dict:
        """Issue ONE credit note against one invoice line. Only after the developer explicitly approved this exact credit (line and amount) with confirm_with_developer, and only when current policy lets Support issue it. Never retried automatically.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice id.
            line_id: The single line being credited (for a duplicate: the copy line, never the original).
            amount: Dollar amount as the console shows it, e.g. "180.00" (the full line amount for a duplicate).
            reason: A credit_notes reason enum value from the console schema (e.g. "duplicate").
            memo: Short note: what was credited and why, with the evidence (request ids).
            extra_fields: Additional fields the current console schema requires, if any.
        """
        cents = self._parse_amount(amount)
        reasons = self._enum("credit_notes", "reason")
        if reasons and reason not in reasons:
            raise ValueError(f"reason must be one of {reasons}")
        account = self._resolve_account(self._text(account_id, "account_id"), None)
        if account is None:
            raise ValueError(f"no account {account_id!r}")
        contracts = self._all("contracts", {"account_id": account["account_id"]})
        if account.get("plan") != "self_serve" or contracts:
            raise ValueError("Support Charter: Support issues credit notes only on self-serve accounts without a "
                             "signed contract. For a confirmed duplicate here, open a dispute on the copy line and "
                             "hand the case to Billing Ops with the dispute id.")
        line_id = self._text(line_id, "line_id")
        lines = self._all("invoice_lines", {"line_id": line_id})
        if not lines or lines[0].get("invoice_id") != invoice_id:
            raise ValueError(f"line {line_id} is not a line of invoice {invoice_id}")
        already = sum(money_of(n, "amount", "amount_cents") for n in self._all("credit_notes", {"invoice_id": invoice_id})
                      if line_id in (n.get("line_ids") or []))
        if already:
            raise ValueError(f"{line_id} already has credit notes totalling {money_text(already)}; do not credit it again")
        if cents > money_of(lines[0], "amount", "amount_cents"):
            raise ValueError("amount exceeds the line's amount")
        row = {
            self._acct("credit_notes"): account["account_id"],
            "invoice_id": self._text(invoice_id, "invoice_id"),
            "line_ids": [line_id],
            **self._money_field("credit_notes", "amount", "amount_cents", cents),
            "reason": reason,
            "memo": (memo or "")[:500],
            **(extra_fields or {}),
        }
        note = self._console().insert("credit_notes", row)
        return {"issued": True, "credit_note": note, "amount": money_text(money_of(note, "amount", "amount_cents"))}

    @is_tool(ToolType.WRITE)
    def open_dispute(self, account_id: str, invoice_id: str, line_ids: list[str], reason: str,
                     request_ids: list[str], window: str = "", submit: bool = True,
                     extra_fields: dict | None = None) -> dict:
        """Open a billing dispute on invoice line(s) (submitted by default; a submitted dispute is final). Only after the developer approved it with confirm_with_developer. Then hand the case to the owning team with escalate_case including the dispute id.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice id.
            line_ids: The disputed line id(s) (for a duplicate: the copy line).
            reason: A disputes reason enum value (e.g. "duplicate_charge", "incorrect_amount", "unrecognized_usage").
            request_ids: Usage request ids the disputed line(s) bill (must belong to the account).
            window: Time window of the usage in the account's billing timezone, e.g. "2027-03-18 10:00 UTC".
            submit: True to submit now (needs invoice, lines, reason and request ids); False leaves a draft.
            extra_fields: Additional fields the current console schema requires, if any.
        """
        reasons = self._enum("disputes", "reason")
        if reasons and reason not in reasons:
            raise ValueError(f"reason must be one of {reasons}")
        if not isinstance(line_ids, list) or not line_ids:
            raise ValueError("line_ids must be a non-empty list")
        row = {
            self._acct("disputes"): self._text(account_id, "account_id"),
            "invoice_id": self._text(invoice_id, "invoice_id"),
            "line_ids": [self._text(x, "line_id") for x in line_ids],
            "reason": reason,
            "request_ids": [self._text(x, "request_id") for x in (request_ids or [])],
            "window": (window or "")[:200],
            "status": "submitted" if submit else "draft",
            **(extra_fields or {}),
        }
        dispute = self._console().insert("disputes", row)
        return {"dispute": dispute}

    @is_tool(ToolType.WRITE)
    def escalate_case(self, account_id: str, team: str, summary: str, details: dict | None = None,
                      dispute_id: str = "", priority: str = "normal", extra_fields: dict | None = None) -> dict:
        """Hand the case to a Redwood team (creates an escalated case). This ENDS your ability to use the console in this conversation, so do every other write first. Include everything the developer gave and what is missing.

        Args:
            account_id: The account id from the console.
            team: The owning team (a cases.team enum value, e.g. billing_ops, billing_engineering, finance, account_team, partnerships, telemetry, sre, legal, platform).
            summary: One-paragraph summary: what the customer asked, what you found, what the team must do. Never card numbers.
            details: Named intake details as strings, e.g. {"invoice_id": "...", "line_ids": "...", "request_ids": "...", "window_billing_tz": "...", "amount_requested": "...", "missing": "..."}.
            dispute_id: The dispute id, if you opened one.
            priority: Case priority enum value; "normal" for billing handoffs unless a current routing page says "urgent".
            extra_fields: Additional fields the current console schema requires, if any.
        """
        teams = self._enum("cases", "team")
        if teams and team not in teams:
            raise ValueError(f"team must be one of {teams}")
        clean_details = {str(k)[:60]: str(v)[:1000] for k, v in (details or {}).items() if v not in (None, "")}
        account = self._resolve_account(account_id, None)
        if account is None:
            raise ValueError(f"no account {account_id!r}; look the account up first")
        row = {
            self._acct("cases"): account["account_id"],
            "team": team,
            "summary": self._text(summary, "summary", 3000),
            "details": clean_details,
            "dispute_id": dispute_id or None,
            "status": "escalated",
            **(extra_fields or {}),
        }
        if "priority" in self._fields("cases"):
            priorities = self._enum("cases", "priority")
            if priorities and priority not in priorities:
                raise ValueError(f"priority must be one of {priorities}")
            row["priority"] = priority
        case = self._console().insert("cases", row)
        return {"escalated": True, "case_id": case.get("case_id"), "team": case.get("team"),
                "priority": case.get("priority"), "status": case.get("status"),
                "note": "The conversation is now handed off; the console is closed for further calls."}
