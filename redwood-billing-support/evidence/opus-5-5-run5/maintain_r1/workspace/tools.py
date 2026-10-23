"""Actions the billing support agent can take.

Read tools return console rows with money shown as "$1,234.00" plus small derived facts (which line
of a duplicate is the original and which the copy, times in the account's billing timezone, credit
balances from the ledger). Field names and money format come from the live `describe()` (console.py). Write tools never retry: a
failed write is reported with its error code, and the agent re-reads the account before any
second attempt.
"""
from __future__ import annotations

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

import console as C
from knowledge import Knowledge

ACTIVE: "Tools | None" = None  # the toolkit of this conversation's process; hooks read the console through it


def _err(error: Exception) -> dict:
    code = getattr(error, "code", None) or "error"
    return {"ok": False, "error_code": str(code), "error": str(error)[:400]}


class Tools(DeveloperTools, SkillTools, SubagentTools, ToolKitBase):
    def __init__(self) -> None:
        global ACTIVE
        ACTIVE = self
        self._knowledge: Knowledge | None = None

    # ------------------------------------------------------------------ knowledge
    @is_tool(ToolType.READ)
    def search_knowledge(self, query: str, wider: bool = False) -> dict:
        """Search Redwood's internal knowledge (policy pages, Finance docs, tickets, Slack, email) for billing rules and facts.

        Returns two groups: `documents` (Confluence/Drive pages; check `status`, `date` and
        `authority` — published vs draft/archived) and `records` (tickets, threads, emails,
        call notes: historical cases and discussion, not policy by themselves). Search again
        with different words if the passage you need is missing.

        Args:
            query: A specific question or keywords, e.g. "who may issue a duplicate-charge credit and up to what amount".
            wider: Also search records outside the billing index (engineering, product, sales). Use for non-billing facts.
        """
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        if self._knowledge is None:
            self._knowledge = Knowledge(getattr(self, "gateway", None))
        return self._knowledge.search(query, wider=bool(wider))

    # ------------------------------------------------------------------ console reads
    def _account(self, account_id: str) -> dict | None:
        rows = C.query_all(self.billing, "accounts", {"account_id": account_id})
        return rows[0] if rows else None

    @is_tool(ToolType.READ)
    def lookup_account(self, account_id: str = "", org: str = "") -> dict:
        """Find a billing account by account id or org name, with contacts, contracts, credit balances and invoices.

        Args:
            account_id: The console account id, if known.
            org: The customer's org name / org id as they gave it, if no account id.
        """
        account_id, org = (account_id or "").strip(), (org or "").strip()
        if not account_id and not org:
            raise ValueError("give account_id or org")
        rows = []
        if account_id:
            rows = C.query_all(self.billing, "accounts", {"account_id": account_id})
        if not rows and org:
            rows = C.query_all(self.billing, "accounts", {"org": org})
        if not rows and org and not account_id:
            rows = C.query_all(self.billing, "accounts", {"account_id": org})
        if not rows:
            return {"found": False, "note": "No account matches. Ask the developer to check the org or account id."}
        out = []
        for acct in rows:
            aid = acct["account_id"]
            out.append({
                "account": acct,
                "contacts": C.query_all(self.billing, "contacts", {"account_id": aid}),
                "contracts": C.query_all(self.billing, "contracts", {"account_id": aid}),
                "credits": C.credit_balances(self.billing, aid),
                "invoices": [{k: i.get(k) for k in ("invoice_id", "period_start", "period_end", "issued_on", "status")}
                             | {"total": C.usd(C.read_money(i, "total"))}
                             for i in C.query_all(self.billing, "invoices", {"account_id": aid})],
            })
        return {"found": True, "accounts": out}

    @is_tool(ToolType.READ)
    def get_credit_balance(self, account_id: str) -> dict:
        """Credit balances per grant (with expiry) and the total available, from the console's source of truth for credits.

        Args:
            account_id: The account id.
        """
        return C.credit_balances(self.billing, (account_id or "").strip())

    @is_tool(ToolType.READ)
    def get_invoice(self, invoice_id: str) -> dict:
        """Read one invoice: header, lines, credit notes against it, and for lines billing the same request ids which is the original and which the copy.

        Args:
            invoice_id: The invoice id as shown in the console.
        """
        invoice_id = (invoice_id or "").strip()
        inv = C.query_all(self.billing, "invoices", {"invoice_id": invoice_id})
        if not inv:
            return {"found": False, "note": "No invoice with that id."}
        header = inv[0]
        lines = C.query_all(self.billing, "invoice_lines", {"invoice_id": invoice_id})
        notes = C.query_all(self.billing, "credit_notes", {"invoice_id": invoice_id})
        credited: dict[str, int] = {}
        for n in notes:
            for lid in n.get("line_ids") or []:
                credited[lid] = credited.get(lid, 0) + (C.read_money(n, "amount") or 0)
        acct = self._account(header.get("account_id", "")) or {}
        return {
            "found": True,
            "invoice": C.display(header),
            "account_billing_timezone": acct.get("billing_timezone") or "UTC",
            "lines": [C.display(line) | {"already_credited": C.usd(credited.get(line["line_id"], 0))} for line in lines],
            "credit_notes": [C.display(n) for n in notes],
            "duplicate_analysis": C.duplicate_analysis(self.billing, lines),
        }

    @is_tool(ToolType.READ)
    def get_usage(self, account_id: str = "", request_ids: list[str] | None = None, line_id: str = "") -> dict:
        """Read usage records by request ids, by invoice line, or all for an account: status, billed/cached tokens, UTC time and time in the account's billing timezone, billing line.

        Args:
            account_id: The account id (needed when listing all usage).
            request_ids: Specific request ids to look up.
            line_id: An invoice line id whose usage you want.
        """
        rows: list[dict] = []
        missing: list[str] = []
        if request_ids:
            for rid in request_ids[:50]:
                found = C.query_all(self.billing, "usage", {"request_id": rid.strip()})
                rows.extend(found) if found else missing.append(rid)
        elif line_id:
            rows = C.query_all(self.billing, "usage", {"line_id": line_id.strip()})
        elif account_id:
            rows = C.query_all(self.billing, "usage", {"account_id": account_id.strip()})
        else:
            raise ValueError("give request_ids, line_id or account_id")
        tz_cache: dict[str, str] = {}
        for r in rows:
            aid = r.get("account_id", "")
            if aid not in tz_cache:
                tz_cache[aid] = (self._account(aid) or {}).get("billing_timezone") or "UTC"
            r["billing_timezone"] = tz_cache[aid]
            r["at_billing_timezone"] = C.local_time(r.get("at", ""), tz_cache[aid])
        return {"rows": rows[:200], "not_found": missing, "truncated": len(rows) > 200}

    @is_tool(ToolType.READ)
    def get_account_activity(self, account_id: str) -> dict:
        """List credit notes, disputes and support cases already on an account (use before any write, and to check whether a write happened).

        Args:
            account_id: The account id.
        """
        aid = (account_id or "").strip()
        return {t: [C.display(r) for r in C.by_account(self.billing, t, aid)]
                for t in ("credit_notes", "disputes", "cases")}

    # ------------------------------------------------------------------ console writes
    @is_tool(ToolType.WRITE)
    def issue_duplicate_credit(self, account_id: str, invoice_id: str, line_id: str, amount: str,
                               memo: str = "") -> dict:
        """Issue one credit note (reason `duplicate`) for the full amount of the copy line of a console-confirmed duplicate. Never retry automatically.

        Only where current policy lets Support credit (account type, limit), against the copy line (the
        one no usage record points to), after the developer said yes to exactly this via confirm_with_developer.

        Args:
            account_id: The account id.
            invoice_id: The invoice id.
            line_id: The copy line to credit.
            amount: The line's full amount in US dollars, e.g. "60.00".
            memo: Short note: invoice, original and copy line ids, request ids.
        """
        cents = C.to_cents(amount)
        name, value = C.money_field(self.billing, "credit_notes", "amount", cents)
        try:
            row = self.billing.insert("credit_notes", {
                C.account_key(self.billing, "credit_notes"): account_id, "invoice_id": invoice_id,
                "line_ids": [line_id], name: value, "reason": "duplicate", "memo": memo or "",
            })
        except BillingError as error:
            return _err(error) | {"next": "Re-read the invoice and account activity before deciding anything else."}
        return {"ok": True, "credit_note": C.display(row)}

    @is_tool(ToolType.WRITE)
    def open_dispute(self, account_id: str, invoice_id: str, line_ids: list[str], reason: str,
                     request_ids: list[str], window: str = "", submit: bool = True) -> dict:
        """Open a billing dispute on invoice lines (submitted by default). Never retry automatically.

        Args:
            account_id: The account id.
            invoice_id: The invoice id.
            line_ids: The disputed line ids (for a duplicate: the copy line only).
            reason: One of duplicate_charge, incorrect_amount, unrecognized_usage.
            request_ids: Usage request ids the disputed lines bill (must belong to this account).
            window: The usage window in the account's billing timezone, if known.
            submit: True to submit now (needs invoice, lines, reason and request ids); False leaves a draft.
        """
        try:
            row = self.billing.insert("disputes", {
                C.account_key(self.billing, "disputes"): account_id, "invoice_id": invoice_id,
                "line_ids": list(line_ids), "reason": reason, "request_ids": list(request_ids),
                "window": window or "", "status": "submitted" if submit else "draft",
            })
        except BillingError as error:
            return _err(error) | {"next": "Check get_account_activity before trying again."}
        return {"ok": True, "dispute": C.display(row)}

    @is_tool(ToolType.WRITE)
    def escalate_case(self, account_id: str, team: str, summary: str, details: dict[str, str] | None = None,
                      dispute_id: str = "", priority: str = "normal") -> dict:
        """Hand the case to a Redwood team. This ends your console access for the conversation, so do every other write first.

        Args:
            account_id: The account id.
            team: One of billing_ops, billing_engineering, finance, legal, account_team, partnerships, telemetry, sre, platform.
            summary: What the customer asked and what you found, in a few sentences.
            details: Intake facts as short strings, e.g. invoice_id, line_ids, request_ids, window (billing timezone), amount, ask, missing.
            dispute_id: The dispute id, if you opened one for this case.
            priority: normal, or urgent only where the routing page calls for it.
        """
        clean = {str(k): str(v) for k, v in (details or {}).items() if v not in (None, "")}
        row = {C.account_key(self.billing, "cases"): account_id, "team": team, "summary": summary,
               "details": clean, "dispute_id": dispute_id or None, "status": "escalated"}
        if "priority" in C.fields(self.billing, "cases") or not C.fields(self.billing, "cases"):
            row["priority"] = priority or "normal"
        try:
            case = self.billing.insert("cases", row)
        except BillingError as error:
            return _err(error)
        return {"ok": True, "case": {k: case.get(k) for k in ("case_id", "team", "status", "priority", "dispute_id")},
                "note": "Handed off. Console calls are closed for this conversation."}
