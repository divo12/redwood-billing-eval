"""Actions the billing support agent can take.

Read tools return console rows as the console stores them (amounts in cents) plus small derived
facts (e.g. which lines on an invoice bill identical request ids). Write tools never retry: a
failed write is reported with its error code, and the agent re-reads the account before any
second attempt.
"""
from __future__ import annotations

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

from knowledge import Knowledge

ACTIVE: "Tools | None" = None  # the toolkit of this conversation's process; hooks read the console through it


def _all(billing, table: str, where: dict) -> list[dict]:
    rows, cursor = [], None
    for _ in range(40):
        page = billing.query(table, where, cursor=cursor) if cursor else billing.query(table, where)
        rows.extend(page.get("rows", []))
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows


def _money(cents: int | None) -> str:
    return "n/a" if cents is None else f"${cents / 100:,.2f}"


def _err(error: Exception) -> dict:
    code = getattr(error, "code", None) or "error"
    return {"ok": False, "error_code": code, "error": str(error)[:400]}


def duplicate_groups(lines: list[dict]) -> list[list[str]]:
    """Groups of line ids on one invoice that bill exactly the same non-empty set of request ids."""
    groups: dict[tuple, list[str]] = {}
    for line in lines:
        ids = tuple(sorted(line.get("request_ids") or []))
        if ids:
            groups.setdefault(ids, []).append(line["line_id"])
    return [g for g in groups.values() if len(g) > 1]


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
    @is_tool(ToolType.READ)
    def lookup_account(self, account_id: str = "", org: str = "") -> dict:
        """Find a billing account by account id or org name, with its contacts, contracts and credit grants.

        Args:
            account_id: The console account id, if known.
            org: The customer's org name / org id as they gave it, if no account id.
        """
        account_id, org = (account_id or "").strip(), (org or "").strip()
        if not account_id and not org:
            raise ValueError("give account_id or org")
        rows = []
        if account_id:
            rows = _all(self.billing, "accounts", {"account_id": account_id})
        if not rows and org:
            rows = _all(self.billing, "accounts", {"org": org})
        if not rows and org and not account_id:
            rows = _all(self.billing, "accounts", {"account_id": org})
        if not rows:
            return {"found": False, "note": "No account matches. Ask the developer to check the org or account id."}
        out = []
        for acct in rows:
            aid = acct["account_id"]
            grants = _all(self.billing, "credit_grants", {"account_id": aid})
            out.append({
                "account": acct,
                "contacts": _all(self.billing, "contacts", {"account_id": aid}),
                "contracts": _all(self.billing, "contracts", {"account_id": aid}),
                "credit_grants": grants,
                "credit_remaining_total": _money(sum(g.get("remaining_cents", 0) for g in grants)),
                "invoices": [{k: i[k] for k in ("invoice_id", "period_start", "period_end", "issued_on", "status")}
                             | {"total": _money(i["total_cents"])}
                             for i in _all(self.billing, "invoices", {"account_id": aid})],
            })
        return {"found": True, "accounts": out}

    @is_tool(ToolType.READ)
    def get_invoice(self, invoice_id: str) -> dict:
        """Read one invoice: header, every line, credit notes already issued against it, and lines that bill identical request ids.

        Args:
            invoice_id: The invoice id as shown in the console.
        """
        invoice_id = (invoice_id or "").strip()
        inv = _all(self.billing, "invoices", {"invoice_id": invoice_id})
        if not inv:
            return {"found": False, "note": "No invoice with that id."}
        header = inv[0]
        lines = _all(self.billing, "invoice_lines", {"invoice_id": invoice_id})
        notes = _all(self.billing, "credit_notes", {"invoice_id": invoice_id})
        credited: dict[str, int] = {}
        for n in notes:
            for lid in n.get("line_ids") or []:
                credited[lid] = credited.get(lid, 0) + int(n.get("amount_cents", 0))
        return {
            "found": True,
            "invoice": header | {"total": _money(header["total_cents"])},
            "lines": [line | {"amount": _money(line["amount_cents"]),
                              "already_credited": _money(credited.get(line["line_id"], 0))} for line in lines],
            "credit_notes": notes,
            "lines_billing_identical_request_ids": duplicate_groups(lines),
        }

    @is_tool(ToolType.READ)
    def get_usage(self, account_id: str = "", request_ids: list[str] | None = None, line_id: str = "") -> dict:
        """Read usage records: by request ids, by invoice line, or all for an account (status, billed and cached tokens, time, line).

        Args:
            account_id: The account id (needed when listing all usage).
            request_ids: Specific request ids to look up.
            line_id: An invoice line id whose usage you want.
        """
        rows: list[dict] = []
        missing: list[str] = []
        if request_ids:
            for rid in request_ids[:50]:
                found = _all(self.billing, "usage", {"request_id": rid.strip()})
                rows.extend(found) if found else missing.append(rid)
        elif line_id:
            rows = _all(self.billing, "usage", {"line_id": line_id.strip()})
        elif account_id:
            rows = _all(self.billing, "usage", {"account_id": account_id.strip()})
        else:
            raise ValueError("give request_ids, line_id or account_id")
        return {"rows": rows[:200], "not_found": missing, "truncated": len(rows) > 200}

    @is_tool(ToolType.READ)
    def get_account_activity(self, account_id: str) -> dict:
        """List credit notes, disputes and support cases already on an account (use before any write, and to check whether a write happened).

        Args:
            account_id: The account id.
        """
        aid = (account_id or "").strip()
        return {
            "credit_notes": _all(self.billing, "credit_notes", {"account_id": aid}),
            "disputes": _all(self.billing, "disputes", {"account_id": aid}),
            "cases": _all(self.billing, "cases", {"account_id": aid}),
        }

    # ------------------------------------------------------------------ console writes
    @is_tool(ToolType.WRITE)
    def issue_duplicate_credit(self, account_id: str, invoice_id: str, line_id: str, amount_cents: int,
                               memo: str = "") -> dict:
        """Issue one credit note (reason `duplicate`) for the full amount of a console-confirmed duplicated line. Never retry automatically.

        Only for a line that bills the same request ids as another line on the same direct invoice,
        within the limit current policy gives Support, after the developer approved it with
        confirm_with_developer.

        Args:
            account_id: The account id.
            invoice_id: The invoice id.
            line_id: The duplicated line to credit (the second, repeated line).
            amount_cents: The line's full amount in cents.
            memo: Short note: invoice, both line ids and the request ids.
        """
        try:
            row = self.billing.insert("credit_notes", {
                "account_id": account_id, "invoice_id": invoice_id, "line_ids": [line_id],
                "amount_cents": int(amount_cents), "reason": "duplicate", "memo": memo or "",
            })
        except BillingError as error:
            return _err(error) | {"next": "Re-read the invoice and account activity before deciding anything else."}
        return {"ok": True, "credit_note": row, "amount": _money(row.get("amount_cents"))}

    @is_tool(ToolType.WRITE)
    def open_dispute(self, account_id: str, invoice_id: str, line_ids: list[str], reason: str,
                     request_ids: list[str], window: str = "", submit: bool = True) -> dict:
        """Open a billing dispute on invoice lines (submitted by default). Never retry automatically.

        Args:
            account_id: The account id.
            invoice_id: The invoice id.
            line_ids: The disputed line ids.
            reason: One of duplicate_charge, incorrect_amount, unrecognized_usage.
            request_ids: Usage request ids the disputed lines bill (must belong to this account).
            window: The UTC time window of the usage, if known.
            submit: True to submit now (needs invoice, lines, reason and request ids); False leaves a draft.
        """
        try:
            row = self.billing.insert("disputes", {
                "account_id": account_id, "invoice_id": invoice_id, "line_ids": list(line_ids),
                "reason": reason, "request_ids": list(request_ids), "window": window or "",
                "status": "submitted" if submit else "draft",
            })
        except BillingError as error:
            return _err(error) | {"next": "Check get_account_activity before trying again."}
        return {"ok": True, "dispute": row}

    @is_tool(ToolType.WRITE)
    def escalate_case(self, account_id: str, team: str, summary: str, details: dict[str, str] | None = None,
                      dispute_id: str = "") -> dict:
        """Hand the case to a Redwood team. This ends your console access for the conversation, so do every other write first.

        Args:
            account_id: The account id.
            team: One of billing_ops, billing_engineering, finance, legal, account_team, partnerships, telemetry, sre, platform.
            summary: What the customer asked and what you found, in a few sentences.
            details: Intake facts as short strings, e.g. invoice_id, line_ids, request_ids, utc_window, amount, ask, missing.
            dispute_id: The dispute id, if you opened one for this case.
        """
        clean = {str(k): str(v) for k, v in (details or {}).items() if v not in (None, "")}
        try:
            case = self.billing.insert("cases", {
                "account_id": account_id, "team": team, "summary": summary, "details": clean,
                "dispute_id": dispute_id or None, "status": "escalated",
            })
        except BillingError as error:
            return _err(error)
        return {"ok": True, "case": {k: case.get(k) for k in ("case_id", "team", "status", "dispute_id")},
                "note": "Handed off. Console calls are closed for this conversation."}
