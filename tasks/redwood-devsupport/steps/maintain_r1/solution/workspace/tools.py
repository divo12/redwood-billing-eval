from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Literal

from billing_rules import DUPLICATE_CREDIT_LIMIT_CENTS
from redwood_kit.developer import DeveloperTools
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

Team = Literal[
    "billing_ops",
    "billing_engineering",
    "finance",
    "legal",
    "account_team",
    "partnerships",
    "telemetry",
    "sre",
    "platform",
]


class Tools(DeveloperTools, SkillTools, SubagentTools, ToolKitBase):
    def __init__(self) -> None:
        self._description: dict | None = None

    def _describe(self) -> dict:
        if self._description is None:
            self._description = self.billing.describe()
        return self._description

    def _query_all(self, table: str, where: dict) -> list[dict]:
        page = self.billing.query(table, where)
        rows = list(page["rows"])
        while page.get("next_cursor"):
            page = self.billing.query(table, where, page["next_cursor"])
            rows.extend(page["rows"])
        return rows

    def _copy_lines(self, grouped: list[dict], requests: frozenset[str]) -> list[str]:
        billed = {
            row.get("line_id")
            for request_id in requests
            for row in self._query_all("usage", {"request_id": request_id})
        }
        copies = sorted(line["line_id"] for line in grouped if line["line_id"] not in billed)
        return copies if len(copies) == len(grouped) - 1 else []

    def _money_field(self, table: str, field: str) -> str:
        fields = self._describe()[table]["fields"]
        cents = f"{field}_cents"
        return cents if cents in fields else field

    def _money_cents(self, table: str, field: str, row: dict) -> int:
        value = row[self._money_field(table, field)]
        if isinstance(value, dict):
            if value.get("currency") != "USD":
                raise ValueError(f"{field} is not denominated in USD")
            return int(Decimal(value["value"]) * 100)
        return int(value)

    @staticmethod
    def _dollars(cents: int) -> str:
        sign = "-" if cents < 0 else ""
        cents = abs(cents)
        return f"{sign}${cents // 100}.{cents % 100:02d}"

    def _money_input(self, table: str, field: str, cents: int) -> tuple[str, object]:
        name = self._money_field(table, field)
        if name.endswith("_cents"):
            return name, cents
        return name, {"value": f"{cents // 100}.{cents % 100:02d}", "currency": "USD"}

    @staticmethod
    def _account_id(rows: list[dict], selector: str) -> dict:
        if not rows:
            raise ValueError(f"no account matched {selector}")
        if len(rows) != 1:
            raise ValueError(f"account selector {selector} matched more than one account")
        return rows[0]

    @is_tool(ToolType.READ)
    def search_policy(self, query: str) -> dict:
        """Search retrieved internal evidence for billing policy and procedures.

        Args:
            query: Policy question or relevant terms to search.
        """
        from retrieval import search_policy

        return {"results": search_policy(query, self.gateway)}

    @is_tool(ToolType.READ)
    def find_account(self, org: str = "", account_id: str = "") -> dict:
        """Find an account by organization or account identifier and return its terms and credits.

        Args:
            org: Customer organization name.
            account_id: Account identifier.
        """
        where = {}
        if org:
            where["org"] = org
        if account_id:
            where["account_id"] = account_id
        if not where:
            raise ValueError("provide an organization name or account identifier")
        selector = (
            f"organization {org!r} and account {account_id!r}"
            if org and account_id
            else f"organization {org!r}" if org else f"account {account_id!r}"
        )
        account = self._account_id(self._query_all("accounts", where), selector)
        resolved_id = account["account_id"]
        return {
            "account": account,
            "contracts": self._query_all("contracts", {"account_id": resolved_id}),
            "credits": self.credit_balances(resolved_id)["credits"],
        }

    @is_tool(ToolType.READ)
    def credit_balances(self, account_id: str) -> dict:
        """Current balance, expiry, and history of each credit grant, from the credit ledger.

        Args:
            account_id: Account identifier returned by find_account.

        Source: R1/sources/confluence/customer-success-and-support/support-process/support-console-credit-ledger-2027.json.
        """
        if "credit_ledger" not in self._describe():
            grants = self._query_all("credit_grants", {"account_id": account_id})
            return {"source": "credit_grants", "credits": grants}
        grants: dict[str, dict] = {}
        for entry in sorted(
            self._query_all("credit_ledger", {"account_id": account_id}),
            key=lambda row: (row["on"], row["entry_id"]),
        ):
            grant = grants.setdefault(
                entry["grant_id"],
                {"grant_id": entry["grant_id"], "kind": entry["credit_kind"],
                 "balance_cents": 0, "expires_on": None, "history": []},
            )
            grant["balance_cents"] += self._money_cents("credit_ledger", "amount", entry)
            if entry["expires_on"]:
                grant["expires_on"] = entry["expires_on"]
            grant["history"].append(
                {"entry": entry["entry"], "on": entry["on"],
                 "amount": self._dollars(self._money_cents("credit_ledger", "amount", entry)),
                 "invoice_id": entry["invoice_id"]}
            )
        credits = []
        for grant in grants.values():
            balance = grant.pop("balance_cents")
            credits.append({**grant, "balance": self._dollars(balance)})
        return {"source": "credit_ledger", "credits": credits}

    def _is_contract_account(self, account_id: str) -> bool:
        # Source: R1/sources/confluence/customer-success-and-support/support-process/support-charter-2027.json.
        accounts = self._query_all("accounts", {"account_id": account_id})
        account = self._account_id(accounts, f"account {account_id!r}")
        return account["plan"] == "enterprise" or bool(
            self._query_all("contracts", {"account_id": account_id})
        )

    @is_tool(ToolType.READ)
    def list_invoices(self, account_id: str) -> dict:
        """List every invoice for an account, including customer-readable totals.

        Args:
            account_id: Account identifier returned by find_account.
        """
        invoices = self._query_all("invoices", {"account_id": account_id})
        for invoice in invoices:
            invoice["total_dollars"] = self._dollars(
                self._money_cents("invoices", "total", invoice)
            )
        return {"invoices": invoices}

    @is_tool(ToolType.READ)
    def invoice_lines(self, invoice_id: str) -> dict:
        """Inspect invoice lines and compare request IDs for duplicates or lookalikes.

        Args:
            invoice_id: Invoice identifier.
        """
        invoices = self._query_all("invoices", {"invoice_id": invoice_id})
        if len(invoices) != 1:
            raise ValueError(f"invoice {invoice_id!r} was not found")
        invoice = invoices[0]
        accounts = self._query_all("accounts", {"account_id": invoice["account_id"]})
        account = self._account_id(accounts, f"account {invoice['account_id']!r}")
        lines = self._query_all("invoice_lines", {"invoice_id": invoice_id})
        for line in lines:
            line["amount_dollars"] = self._dollars(
                self._money_cents("invoice_lines", "amount", line)
            )

        groups: dict[frozenset[str], list[dict]] = defaultdict(list)
        for line in lines:
            requests = frozenset(line.get("request_ids", []))
            if requests:
                groups[requests].append(line)
        confirmed_duplicates = [
            {
                "line_ids": sorted(line["line_id"] for line in grouped),
                "request_ids": sorted(requests),
                "copy_line_ids": self._copy_lines(grouped, requests),
            }
            for requests, grouped in groups.items()
            if len(grouped) > 1
        ]

        lookalikes = []
        for index, first in enumerate(lines):
            first_requests = set(first.get("request_ids", []))
            for second in lines[index + 1 :]:
                second_requests = set(second.get("request_ids", []))
                if (
                    first_requests
                    and second_requests
                    and first_requests != second_requests
                    and self._money_cents("invoice_lines", "amount", first)
                    == self._money_cents("invoice_lines", "amount", second)
                ):
                    lookalikes.append(
                        {
                            "line_ids": [first["line_id"], second["line_id"]],
                            "request_ids": {
                                first["line_id"]: sorted(first_requests),
                                second["line_id"]: sorted(second_requests),
                            },
                            "amount_dollars": first["amount_dollars"],
                        }
                    )
        return {
            "invoice": invoice,
            "billing_channel": account["billing_channel"],
            "lines": lines,
            "confirmed_duplicates": confirmed_duplicates,
            "lookalikes": lookalikes,
        }

    @is_tool(ToolType.READ)
    def usage_summary(
        self,
        account_id: str,
        start_utc: str = "",
        end_utc: str = "",
        request_ids: list[str] | None = None,
    ) -> dict:
        """Summarize usage requests and token totals in an optional inclusive UTC window.

        Args:
            account_id: Account identifier returned by find_account.
            start_utc: Inclusive UTC start timestamp.
            end_utc: Inclusive UTC end timestamp.
            request_ids: Request IDs to look up exactly; each is reported as recorded or not.
        """
        lookups = {
            request_id: [
                row for row in self._query_all("usage", {"request_id": request_id})
                if row["account_id"] == account_id
            ]
            for request_id in request_ids or []
        }
        rows = self._query_all("usage", {"account_id": account_id})
        rows = [
            row for row in rows
            if (not start_utc or row["at"] >= start_utc)
            and (not end_utc or row["at"] <= end_utc)
        ]
        status_counts: dict[str, int] = defaultdict(int)
        status_tokens: dict[str, int] = defaultdict(int)
        for row in rows:
            status_counts[str(row["http_status"])] += 1
            status_tokens[str(row["http_status"])] += row["billed_tokens"]
        return {
            "count": len(rows),
            "billed_tokens": sum(row["billed_tokens"] for row in rows),
            "cached_tokens": sum(row["cached_tokens"] for row in rows),
            "http_status_counts": dict(sorted(status_counts.items())),
            "billed_tokens_by_http_status": dict(sorted(status_tokens.items())),
            "start_utc": start_utc or None,
            "end_utc": end_utc or None,
            "requests": {
                request_id: rows[0] if rows else "not recorded for this account"
                for request_id, rows in lookups.items()
            },
        }

    def _confirmed_duplicate_line(
        self, account_id: str, invoice_id: str, line_id: str
    ) -> tuple[dict, dict]:
        # Source: R1/sources/confluence/customer-success-and-support/support-process/support-console-billing-credits-and-disputes-v2.json.
        inspected = self.invoice_lines(invoice_id)
        if inspected["invoice"]["account_id"] != account_id:
            raise ValueError("invoice does not belong to this account")
        if inspected["billing_channel"] != "direct":
            raise ValueError(
                "only direct-billed invoices qualify for this console action; "
                "follow current marketplace and billing-routing guidance instead"
            )
        line = next(
            (item for item in inspected["lines"] if item["line_id"] == line_id),
            None,
        )
        if line is None:
            raise ValueError(
                "line does not belong to this invoice; follow current "
                "billing-routing guidance instead"
            )
        duplicate = next(
            (
                group
                for group in inspected["confirmed_duplicates"]
                if line_id in group["line_ids"]
            ),
            None,
        )
        if duplicate is None:
            if any(line_id in pair["line_ids"] for pair in inspected["lookalikes"]):
                raise ValueError(
                    "same-amount lines with different request IDs are lookalikes, "
                    "not confirmed duplicates; follow current billing-routing "
                    "guidance instead"
                )
            raise ValueError(
                "line is not part of a confirmed duplicate; follow current "
                "billing-routing guidance instead"
            )
        return line, duplicate

    @is_tool(ToolType.WRITE)
    def credit_duplicate(
        self, account_id: str, invoice_id: str, line_id: str
    ) -> dict:
        """Credit the full amount of one verified duplicate line when eligible.

        Args:
            account_id: Account identifier returned by find_account.
            invoice_id: Invoice containing the confirmed duplicate.
            line_id: Duplicate line to credit.
        """
        line, _ = self._confirmed_duplicate_line(account_id, invoice_id, line_id)
        if self._is_contract_account(account_id):
            raise ValueError(
                "Support may not credit an account with a signed contract; open a "
                "duplicate-charge dispute and escalate it to billing_ops"
            )
        amount_cents = self._money_cents("invoice_lines", "amount", line)
        if amount_cents > DUPLICATE_CREDIT_LIMIT_CENTS:
            raise ValueError(
                f"{self._dollars(amount_cents)} exceeds the console credit limit; "
                "open a duplicate-charge dispute and escalate it to billing_ops"
            )
        existing = self._query_all("credit_notes", {"invoice_id": invoice_id})
        if any(line_id in note["line_ids"] for note in existing):
            raise ValueError("a credit note already exists for this invoice line")
        amount_field, amount = self._money_input(
            "credit_notes", "amount", amount_cents
        )
        note = self.billing.insert(
            "credit_notes",
            {
                "account_id": account_id,
                "invoice_id": invoice_id,
                "line_ids": [line_id],
                amount_field: amount,
                "reason": "duplicate",
            },
        )
        return {
            "credit_note": note,
            "amount_credited": self._dollars(amount_cents),
        }

    @is_tool(ToolType.WRITE)
    def open_dispute(
        self,
        account_id: str,
        invoice_id: str,
        line_id: str,
    ) -> dict:
        """Submit a confirmed-duplicate dispute: over the console limit, or on a contract account.

        Args:
            account_id: Account identifier returned by find_account.
            invoice_id: Invoice containing the confirmed duplicate.
            line_id: Duplicate line to dispute.

        Source: R0/sources/confluence/customer-success-and-support/support-process/support-console-billing-credits-and-disputes.json.
        """
        line, duplicate = self._confirmed_duplicate_line(
            account_id, invoice_id, line_id
        )
        amount_cents = self._money_cents("invoice_lines", "amount", line)
        if amount_cents <= DUPLICATE_CREDIT_LIMIT_CENTS and not self._is_contract_account(account_id):
            raise ValueError(
                "confirmed duplicates within the current console limit should use "
                "credit_duplicate; route other requests under current billing guidance"
            )
        return self.billing.insert(
            "disputes",
            {
                "account_id": account_id,
                "invoice_id": invoice_id,
                "line_ids": [line_id],
                "reason": "duplicate_charge",
                "request_ids": duplicate["request_ids"],
                "status": "submitted",
            },
        )

    @is_tool(ToolType.WRITE)
    def escalate_case(
        self,
        account_id: str,
        team: Team,
        summary: str,
        details: dict[str, str],
        unresolved: str,
        routing_rule: str,
        dispute_id: str | None = None,
        priority: Literal["normal", "urgent"] = "normal",
    ) -> dict:
        """Escalate a support case with retrieved routing evidence.

        Args:
            account_id: Account identifier returned by find_account.
            team: Receiving team.
            summary: Short case summary.
            details: Case facts, including invoice and request context when available.
            unresolved: What Support cannot resolve in this conversation.
            routing_rule: Verbatim excerpt from a search_policy result naming team.
            dispute_id: Submitted dispute associated with the case.
            priority: Case priority required by the current console.

        Source: R0/sources/confluence/customer-success-and-support/support-process/billing-case-routing-and-handoffs.json.
        """
        case_details = {
            **details,
            "unresolved": unresolved,
            "routing_rule": routing_rule,
        }
        case = self.billing.insert(
            "cases",
            {
                "account_id": account_id,
                "team": team,
                "summary": summary,
                "details": case_details,
                "dispute_id": dispute_id,
                "priority": priority,
                "status": "escalated",
            },
        )
        return {
            "case_id": case["case_id"],
            "team": case["team"],
            "status": case["status"],
            "priority": case["priority"],
        }
