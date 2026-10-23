"""Actions the billing support agent can take: knowledge search, console reads, console writes.

Console writes are final (no undo). Hooks in hooks.py guard them; the tools themselves validate
their inputs because the runtime does not.
"""
from __future__ import annotations

from redwood_kit.billing import BillingError
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools, load_subagents
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

from ledger import LEDGER

LEDGER_ROOT = __import__("pathlib").Path(__file__).resolve().parent
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


class Tools(DeveloperTools, SkillTools, SubagentTools, ToolKitBase):
    _retriever = None

    def __init__(self) -> None:
        LEDGER.toolkit = self

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
                credited[line] = credited.get(line, 0) + int(note.get("amount_cents", 0)) // max(1, len(lines))
        return credited

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
        """Search Redwood's internal knowledge (policies, procedures, tickets, threads) for passages.

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
        """Look up a billing account by account id or org name, with its plan, billing channel,
        contracts, credit grants, invoices, credit notes, disputes and cases.

        Args:
            account_id: The account id, if known.
            org: The org name or id the developer gave, if no account id.
        """
        account = self._account(account_id, org)
        if account is None:
            return {"found": False, "searched": {"account_id": account_id, "org": org},
                    "note": "No account matches. Ask the developer for their org or account id."}
        aid = account["account_id"]
        grants = self._all("credit_grants", {"account_id": aid})
        return {
            "found": True,
            "account": account,
            "contacts": [{k: c.get(k) for k in ("name", "role")} for c in self._all("contacts", {"account_id": aid})],
            "contracts": self._all("contracts", {"account_id": aid}),
            "credit_grants": grants,
            "credit_balance_cents": sum(int(g.get("remaining_cents", 0)) for g in grants),
            "invoices": self._all("invoices", {"account_id": aid}),
            "credit_notes": self._all("credit_notes", {"account_id": aid}),
            "disputes": self._all("disputes", {"account_id": aid}),
            "cases": self._all("cases", {"account_id": aid}),
        }

    @is_tool(ToolType.READ)
    def invoice_details(self, invoice_id: str) -> dict:
        """Read one invoice with its lines, credits already issued per line, and the console's duplicate check.

        `console_confirmed_duplicates` lists groups of lines on this invoice that bill the same request
        ids; `lookalike_lines` lists lines with the same description and amount but different request
        ids (not duplicates by the console).

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
            line["credited_cents"] = credited.get(line["line_id"], 0)
            line["uncredited_cents"] = int(line.get("amount_cents", 0)) - line["credited_cents"]
        groups: dict[tuple, list[dict]] = {}
        for line in lines:
            key = tuple(sorted(line.get("request_ids") or []))
            if key:
                groups.setdefault(key, []).append(line)
        duplicates = [{"line_ids": [l["line_id"] for l in group], "shared_request_ids": list(key),
                       "amounts_cents": [l["amount_cents"] for l in group]}
                      for key, group in groups.items() if len(group) > 1]
        looks: dict[tuple, list[dict]] = {}
        for line in lines:
            looks.setdefault((line.get("description"), line.get("amount_cents")), []).append(line)
        lookalikes = [{"line_ids": [l["line_id"] for l in group],
                       "request_ids": {l["line_id"]: l.get("request_ids") for l in group}}
                      for group in looks.values()
                      if len(group) > 1 and len({tuple(sorted(l.get("request_ids") or [])) for l in group}) > 1]
        account = self._account(invoice.get("account_id"), None) or {}
        return {
            "found": True,
            "invoice": invoice,
            "account": {k: account.get(k) for k in ("account_id", "org", "plan", "plan_name", "billing_channel",
                                                    "payment_method", "status")},
            "lines": lines,
            "console_confirmed_duplicates": duplicates,
            "lookalike_lines": lookalikes,
            "credit_notes": self._all("credit_notes", {"invoice_id": invoice_id}),
        }

    @is_tool(ToolType.READ)
    def usage_lookup(self, request_ids: list[str]) -> dict:
        """Look up usage records by request id: time (UTC), HTTP status, billed and cached tokens, and the
        invoice line that bills each one (null if not billed on a Redwood invoice line).

        Args:
            request_ids: Request ids the developer gave.
        """
        found, missing = [], []
        for request_id in _ids(request_ids, "request_ids")[:20]:
            rows = self.billing.query("usage", {"request_id": request_id}).get("rows", [])
            if not rows:
                missing.append(request_id)
                continue
            row = rows[0]
            if row.get("line_id"):
                line = self.billing.query("invoice_lines", {"line_id": row["line_id"]}).get("rows", [])
                row["invoice_id"] = line[0]["invoice_id"] if line else None
            found.append(row)
        return {"usage": found, "not_found": missing}

    @is_tool(ToolType.READ)
    def console_query(self, table: str, filters: dict[str, str]) -> dict:
        """Read rows from any billing console table (see describe for tables and selectors).

        Args:
            table: Table name, e.g. accounts, invoices, invoice_lines, usage, credit_grants, contracts,
                credit_notes, disputes, cases.
            filters: Field values to match; must include at least one selector of the table.
        """
        table = _text(table, "table", 50)
        if not isinstance(filters, dict) or not filters:
            raise ValueError("filters must be an object with at least one selector")
        return {"rows": self._all(table, filters)[:100]}

    @is_tool(ToolType.READ)
    def console_schema(self) -> dict:
        """Describe the billing console: tables, fields, enums, selectors and which tables are writable."""
        return self.billing.describe()

    # ------------------------------------------------------------------ console writes

    @is_tool(ToolType.WRITE)
    def issue_credit_note(
        self, account_id: str, invoice_id: str, line_ids: list[str], amount_cents: int, reason: str,
        memo: str, policy_source: str, policy_limit: str,
    ) -> dict:
        """Issue a credit note against invoice lines. Final; never retry after an error without checking
        invoice_details first. Requires the developer's approval via confirm_with_developer.

        Args:
            account_id: The account id from the console.
            invoice_id: The invoice the lines are on.
            line_ids: The line(s) being credited.
            amount_cents: Credit amount in cents.
            reason: duplicate, service_credit or goodwill.
            memo: Short note: what is credited and why (evidence).
            policy_source: Source id of the retrieved policy passage that lets Support issue this credit.
            policy_limit: Support's limit exactly as that passage states it, e.g. "$X per duplicated line".
        """
        row = {
            "account_id": _text(account_id, "account_id"),
            "invoice_id": _text(invoice_id, "invoice_id"),
            "line_ids": _ids(line_ids, "line_ids"),
            "amount_cents": int(amount_cents),
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
        Requires the developer's approval via confirm_with_developer.

        Args:
            account_id: The account id from the console.
            invoice_id: The disputed invoice.
            line_ids: The disputed line(s).
            reason: duplicate_charge, incorrect_amount or unrecognized_usage.
            request_ids: Request ids the disputed lines bill (from the console).
            window: UTC time window of the disputed usage, if known.
            submit: True to submit now; False to leave a draft.
        """
        row = {
            "account_id": _text(account_id, "account_id"),
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
        dispute_id: str = "",
    ) -> dict:
        """Hand the case to a Redwood team. This ends your access to the console for this conversation,
        so answer everything you can and make all other writes first. Requires the developer's approval.

        Args:
            account_id: The account id from the console.
            team: billing_ops, billing_engineering, finance, legal, account_team, partnerships, telemetry,
                sre or platform.
            summary: What the developer asked for and what you found, in a few sentences.
            details: Intake facts as name -> value (invoice_id, line_ids, request_ids, amounts, utc_window,
                error_code, plan, contract, missing details, policy sources...).
            dispute_id: The dispute id if you opened one.
        """
        team = _text(team, "team", 40)
        if team not in CASE_TEAMS:
            raise ValueError(f"team must be one of {CASE_TEAMS}")
        clean = {str(k)[:60]: str(v)[:500] for k, v in (details or {}).items()} if isinstance(details, dict) else {}
        account_id = _text(account_id, "account_id")
        # Link the dispute opened in this conversation even if the model put its id only in details.
        dispute = (dispute_id.strip() if isinstance(dispute_id, str) else "") or clean.get("dispute_id", "").strip()
        if not dispute:
            opened = [w["dispute"] for w in LEDGER.writes if "dispute" in w
                      and w["dispute"].get("account_id") == account_id]
            dispute = opened[-1].get("dispute_id", "") if opened else ""
        case = self.billing.insert("cases", {
            "account_id": account_id,
            "team": team,
            "summary": _text(summary, "summary", 2000),
            "details": clean,
            "dispute_id": dispute or None,
            "status": "escalated",
        })
        LEDGER.escalated = True
        LEDGER.writes.append({"case": case})
        return {"case": {k: case.get(k) for k in ("case_id", "team", "status", "dispute_id")}}
