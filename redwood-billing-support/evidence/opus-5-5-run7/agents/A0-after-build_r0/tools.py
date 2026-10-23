"""Actions the billing support agent can take.

Reads: knowledge search over the shipped index, and billing-console lookups that return the facts
Support needs (account, contracts, credits, invoices with duplicate analysis, usage).
Writes: credit note, dispute, escalated case. Writes are never retried automatically; a write that
errors must be checked with a read before anything else is attempted (see hooks.py).
"""
from __future__ import annotations

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

from retrieval import Retriever

MAX_PAGES = 8


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

    def _all(self, table: str, where: dict) -> list[dict]:
        rows, cursor = [], None
        for _ in range(MAX_PAGES):
            page = self._console().query(table, where, cursor)
            rows.extend(page.get("rows", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows

    def _describe(self) -> dict:
        if self._schema is None:
            self._schema = self._console().describe()
        return self._schema

    def _enum(self, table: str, field: str) -> list[str]:
        try:
            return list(self._describe()[table]["fields"][field].get("enum") or [])
        except (KeyError, TypeError, BillingError):
            return []

    @staticmethod
    def _text(value, name: str, limit: int = 200) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} must be a non-empty string of at most {limit} characters")
        return value.strip()

    @staticmethod
    def _dollars(cents) -> str:
        return f"${cents / 100:,.2f}" if isinstance(cents, int) else str(cents)

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

    # ------------------------------------------------------------------ knowledge

    @is_tool(ToolType.READ)
    def search_knowledge(self, query: str, include_archive: bool = False) -> dict:
        """Search Redwood's internal knowledge (policy pages, Finance matrices, support procedures, past tickets, Slack/email) for passages relevant to a billing question.

        Each result has source, kind, title, status (published/final/draft/in_review/ARCHIVED...) and date. Prefer current published policy; drafts, proposals, archived pages and old threads are not current rules. Run several focused searches for multi-part questions.

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
        """Look up a customer's billing account by account id or org name, with its contracts, credit grants, invoices, and any existing credit notes, disputes and cases.

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
        for table in ("contracts", "credit_grants", "invoices", "credit_notes", "disputes", "cases"):
            try:
                out[table] = self._all(table, {"account_id": aid})
            except BillingError as error:
                out[table] = f"unavailable: {error}"
        grants = out.get("credit_grants")
        if isinstance(grants, list) and grants:
            out["credit_balance_cents"] = sum(int(g.get("remaining_cents") or 0) for g in grants)
        if isinstance(out.get("invoices"), list):
            for inv in out["invoices"]:
                inv["total"] = self._dollars(inv.get("total_cents"))
        return out

    @is_tool(ToolType.READ)
    def get_invoice(self, invoice_id: str) -> dict:
        """Get one invoice with all its lines, credit notes already issued against it, and a duplicate analysis (lines that bill the identical request ids).

        Args:
            invoice_id: The invoice id exactly as in the console.
        """
        invoice_id = self._text(invoice_id, "invoice_id")
        headers = self._all("invoices", {"invoice_id": invoice_id})
        if not headers:
            return {"found": False, "invoice_id": invoice_id,
                    "next_step": "Check the id with the developer or list the account's invoices with lookup_account."}
        invoice = headers[0]
        lines = self._all("invoice_lines", {"invoice_id": invoice_id})
        notes = self._all("credit_notes", {"invoice_id": invoice_id})
        credited: dict[str, int] = {}
        for note in notes:
            for lid in note.get("line_ids") or []:
                credited[lid] = credited.get(lid, 0) + int(note.get("amount_cents") or 0)
        for line in lines:
            line["amount"] = self._dollars(line.get("amount_cents"))
            line["credited_cents"] = credited.get(line["line_id"], 0)
            line["uncredited_cents"] = int(line.get("amount_cents") or 0) - line["credited_cents"]
        groups: dict[tuple, list[dict]] = {}
        for line in lines:
            key = tuple(sorted(line.get("request_ids") or []))
            if key:
                groups.setdefault(key, []).append(line)
        duplicates = []
        for key, members in groups.items():
            if len(members) > 1:
                members = sorted(members, key=lambda ln: ln["line_id"])
                duplicates.append({
                    "request_ids": list(key),
                    "line_ids": [m["line_id"] for m in members],
                    "original_line": members[0]["line_id"],
                    "duplicated_lines": [m["line_id"] for m in members[1:]],
                    "amount_cents_each": [m.get("amount_cents") for m in members],
                })
        same_amount = {}
        for line in lines:
            same_amount.setdefault((line.get("amount_cents"), line.get("description")), []).append(line["line_id"])
        lookalikes = [ids for ids in same_amount.values() if len(ids) > 1 and not any(
            set(ids) <= set(d["line_ids"]) for d in duplicates)]
        invoice["total"] = self._dollars(invoice.get("total_cents"))
        return {
            "found": True,
            "invoice": invoice,
            "lines": lines,
            "credit_notes": notes,
            "console_confirmed_duplicates": duplicates,
            "same_amount_but_different_requests": lookalikes,
            "note": ("A console-confirmed duplicate is two lines on this invoice billing the same request ids. "
                     "Lines with the same amount/description but different request ids are NOT confirmed duplicates."),
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
            table: Table name, e.g. "usage", "invoice_lines", "contacts".
            where: Field filters, e.g. {"line_id": "INV-1/L2"}.
            cursor: Continuation cursor from a previous page, if any.
        """
        return self._console().query(self._text(table, "table", 60), where or None, cursor or None)

    @is_tool(ToolType.READ)
    def console_schema(self) -> dict:
        """Show the billing console's current tables, fields, selectors, enums and required fields."""
        return self._describe()

    # ------------------------------------------------------------------ console writes

    @is_tool(ToolType.WRITE)
    def issue_credit_note(self, account_id: str, invoice_id: str, line_id: str, amount_cents: int,
                          reason: str, memo: str = "", extra_fields: dict | None = None) -> dict:
        """Issue ONE credit note against one invoice line. Only after the developer approved it with confirm_with_developer, and only when current policy allows Support to issue it. Never retried automatically.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice id.
            line_id: The single line being credited (for a duplicate: the duplicated line).
            amount_cents: Amount in cents, e.g. 18000 for $180.00.
            reason: A credit_notes reason enum value from the console schema (e.g. "duplicate").
            memo: Short note: what was credited and why, with the evidence (request ids).
            extra_fields: Additional fields the current console schema requires, if any.
        """
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
            raise ValueError("amount_cents must be a positive integer number of cents")
        reasons = self._enum("credit_notes", "reason")
        if reasons and reason not in reasons:
            raise ValueError(f"reason must be one of {reasons}")
        line_id = self._text(line_id, "line_id")
        lines = self._all("invoice_lines", {"line_id": line_id})
        if not lines or lines[0].get("invoice_id") != invoice_id:
            raise ValueError(f"line {line_id} is not a line of invoice {invoice_id}")
        already = sum(int(n.get("amount_cents") or 0) for n in self._all("credit_notes", {"invoice_id": invoice_id})
                      if line_id in (n.get("line_ids") or []))
        if already:
            raise ValueError(f"{line_id} already has credit notes totalling {already} cents; do not credit it again")
        if amount_cents > int(lines[0].get("amount_cents") or 0):
            raise ValueError("amount exceeds the line's amount")
        row = {
            "account_id": self._text(account_id, "account_id"),
            "invoice_id": self._text(invoice_id, "invoice_id"),
            "line_ids": [line_id],
            "amount_cents": amount_cents,
            "reason": reason,
            "memo": (memo or "")[:500],
            **(extra_fields or {}),
        }
        note = self._console().insert("credit_notes", row)
        return {"issued": True, "credit_note": note, "amount": self._dollars(note.get("amount_cents"))}

    @is_tool(ToolType.WRITE)
    def open_dispute(self, account_id: str, invoice_id: str, line_ids: list[str], reason: str,
                     request_ids: list[str], window: str = "", submit: bool = True,
                     extra_fields: dict | None = None) -> dict:
        """Open a billing dispute on invoice line(s) (submitted by default; a submitted dispute is final). Use when policy says to dispute rather than credit, then hand the case to the owning team with escalate_case including the dispute id.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice id.
            line_ids: The disputed line id(s).
            reason: A disputes reason enum value (e.g. "duplicate_charge", "incorrect_amount", "unrecognized_usage").
            request_ids: Usage request ids the disputed line(s) bill (must belong to the account).
            window: UTC time window of the usage, if known, e.g. "2026-08-12 10:00-11:00 UTC".
            submit: True to submit now (needs invoice, lines, reason and request ids); False leaves a draft.
            extra_fields: Additional fields the current console schema requires, if any.
        """
        reasons = self._enum("disputes", "reason")
        if reasons and reason not in reasons:
            raise ValueError(f"reason must be one of {reasons}")
        if not isinstance(line_ids, list) or not line_ids:
            raise ValueError("line_ids must be a non-empty list")
        row = {
            "account_id": self._text(account_id, "account_id"),
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
                      dispute_id: str = "", extra_fields: dict | None = None) -> dict:
        """Hand the case to a Redwood team (creates an escalated case). This ENDS your ability to use the console in this conversation, so do every other write first. Include everything the developer gave and what is missing.

        Args:
            account_id: The account id from the console.
            team: The owning team (a cases.team enum value, e.g. billing_ops, billing_engineering, finance, account_team, partnerships, telemetry, sre, legal, platform).
            summary: One-paragraph summary: what the customer asked, what you found, what the team must do.
            details: Named intake details as strings, e.g. {"invoice_id": "...", "line_ids": "...", "request_ids": "...", "utc_window": "...", "amount_requested": "...", "missing": "..."}.
            dispute_id: The dispute id, if you opened one.
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
            "account_id": account["account_id"],
            "team": team,
            "summary": self._text(summary, "summary", 3000),
            "details": clean_details,
            "dispute_id": dispute_id or None,
            "status": "escalated",
            **(extra_fields or {}),
        }
        case = self._console().insert("cases", row)
        return {"escalated": True, "case_id": case.get("case_id"), "team": case.get("team"),
                "status": case.get("status"),
                "note": "The conversation is now handed off; the console is closed for further calls."}
