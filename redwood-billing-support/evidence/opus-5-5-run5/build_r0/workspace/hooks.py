"""Guardrails around every developer message and tool call (including a subagent's).

1. a_mask_card_numbers (user_message): payment card numbers never enter the model context or a case.
2. b_console_closed (pre_tool): after a handoff the console is closed; refuse further console calls.
3. c_require_consent (pre_tool, writes): a write needs the developer's latest confirm_with_developer
   answer to be "yes", and a credit's confirmation must name the line or the amount.
4. d_verify_duplicate_credit (pre_tool, issue_duplicate_credit): the console must show the duplicate
   (two lines on one direct invoice billing the same request ids), the amount must be the line's
   full amount, nothing already credited, and the amount within Support's published limit.
5. e_check_case (pre_tool, escalate_case): one handoff, a known team, a real summary, no card data.
6. z_track (post_tool): remembers confirmations and handoffs.
"""
from __future__ import annotations

import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

WRITE_TOOLS = ("issue_duplicate_credit", "open_dispute", "escalate_case")
CONSOLE_TOOLS = ("lookup_account", "get_invoice", "get_usage", "get_account_activity") + WRITE_TOOLS
TEAMS = {"billing_ops", "billing_engineering", "finance", "legal", "account_team", "partnerships",
         "telemetry", "sre", "platform"}
DISPUTE_REASONS = {"duplicate_charge", "incorrect_amount", "unrecognized_usage"}
CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def mask_cards(text: str) -> str:
    def repl(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return f"[card number ending {digits[-4:]} removed]"
        return m.group(0)
    return CARD.sub(repl, text)


def _money_forms(cents: int) -> list[str]:
    d = cents / 100
    forms = {f"{d:,.2f}", f"{d:.2f}", f"{d:,.0f}" if cents % 100 == 0 else f"{d:,.2f}", f"{d:.0f}" if cents % 100 == 0 else f"{d:.2f}"}
    return sorted(forms)


class Hooks(HookKitBase):
    def __init__(self) -> None:
        self.last_confirmation: dict | None = None
        self.handed_off = False

    def _billing(self):
        import tools
        if tools.ACTIVE is None or tools.ACTIVE.billing is None:
            raise Blocked("billing console unavailable")
        return tools.ACTIVE.billing

    # 1 ---------------------------------------------------------------------------------------
    @hook("user_message")
    def a_mask_card_numbers(self, text: str) -> str | None:
        masked = mask_cards(text)
        return masked if masked != text else None

    # 2 ---------------------------------------------------------------------------------------
    @hook("pre_tool")
    def b_console_closed(self, tool: str, args: dict) -> dict | None:
        if self.handed_off and tool in CONSOLE_TOOLS:
            raise Blocked("This conversation was already handed to a team; the console is closed. "
                          "Answer from what you already know.")
        return None

    # 3 ---------------------------------------------------------------------------------------
    @hook("pre_tool")
    def c_require_consent(self, tool: str, args: dict) -> dict | None:
        if tool not in WRITE_TOOLS:
            return None
        c = self.last_confirmation
        if not c:
            raise Blocked("Get the developer's approval first: call confirm_with_developer with exactly what you "
                          "will do (which line or case, which team, what amount).")
        if str(c.get("answer", "")).strip().lower() != "yes":
            raise Blocked("The developer did not approve this action. Do not perform it; tell them what happens "
                          "instead and ask what they would like.")
        if tool == "issue_duplicate_credit":
            proposal = c.get("proposal", "")
            amount = int(args.get("amount_cents") or 0)
            if args.get("line_id", "") not in proposal and not any(f in proposal for f in _money_forms(amount)):
                raise Blocked("The approval you got does not name this credit. Confirm the exact line and amount "
                              "with confirm_with_developer first.")
        return None

    # 4 ---------------------------------------------------------------------------------------
    @hook("pre_tool", match="issue_duplicate_credit")
    def d_verify_duplicate_credit(self, tool: str, args: dict) -> dict | None:
        billing = self._billing()
        account_id, invoice_id, line_id = (str(args.get(k, "")).strip() for k in ("account_id", "invoice_id", "line_id"))
        try:
            amount = int(args.get("amount_cents"))
        except (TypeError, ValueError):
            raise Blocked("amount_cents must be an integer number of cents") from None
        accounts = billing.query("accounts", {"account_id": account_id}).get("rows", [])
        if not accounts:
            raise Blocked(f"No account {account_id!r}.")
        if accounts[0].get("billing_channel") != "direct":
            raise Blocked("This account is not billed directly by Redwood (marketplace/reseller); Support issues no "
                          "credit note. Explain who adjusts their bill instead.")
        invoices = billing.query("invoices", {"invoice_id": invoice_id}).get("rows", [])
        if not invoices or invoices[0].get("account_id") != account_id:
            raise Blocked("That invoice does not belong to this account.")
        lines = billing.query("invoice_lines", {"invoice_id": invoice_id}).get("rows", [])
        line = next((l for l in lines if l["line_id"] == line_id), None)
        if line is None:
            raise Blocked("That line is not on this invoice.")
        ids = sorted(line.get("request_ids") or [])
        twins = [l for l in lines if l["line_id"] != line_id and ids and sorted(l.get("request_ids") or []) == ids]
        if not twins:
            raise Blocked("The console does not confirm a duplicate: no other line on this invoice bills the same "
                          "request ids. Do not credit; follow the routing for unconfirmed duplicates.")
        if amount != int(line["amount_cents"]):
            raise Blocked(f"A duplicate credit is for the duplicated line's full amount ({line['amount_cents']} "
                          f"cents), never a partial or split amount.")
        group = {line_id, *(t["line_id"] for t in twins)}
        notes = billing.query("credit_notes", {"invoice_id": invoice_id}).get("rows", [])
        if any(group & set(n.get("line_ids") or []) for n in notes):
            raise Blocked("A credit note already exists for this duplicate; do not issue another. Re-read the invoice.")
        from knowledge import duplicate_credit_limit_cents
        limit, source = duplicate_credit_limit_cents()
        if limit is not None and amount > limit:
            raise Blocked(f"This line (${amount / 100:,.2f}) is over Support's duplicate-credit limit "
                          f"(${limit / 100:,.2f} per line, per {source}). Do not credit; open a dispute on the "
                          f"duplicated line and hand the case to the owning team.")
        return None

    # 5 ---------------------------------------------------------------------------------------
    @hook("pre_tool", match="escalate_case")
    def e_check_case(self, tool: str, args: dict) -> dict | None:
        if args.get("team") not in TEAMS:
            raise Blocked(f"team must be one of {sorted(TEAMS)}")
        if not str(args.get("summary", "")).strip():
            raise Blocked("A handoff needs a summary of the ask and what you found.")
        details = {str(k): mask_cards(str(v)) for k, v in (args.get("details") or {}).items()}
        return {**args, "summary": mask_cards(str(args["summary"])), "details": details}

    @hook("pre_tool", match="open_dispute")
    def e_check_dispute(self, tool: str, args: dict) -> dict | None:
        if args.get("reason") not in DISPUTE_REASONS:
            raise Blocked(f"reason must be one of {sorted(DISPUTE_REASONS)}")
        return None

    # 6 ---------------------------------------------------------------------------------------
    @hook("post_tool")
    def z_track(self, tool: str, args: dict, result) -> None:
        if tool == "confirm_with_developer" and isinstance(result, dict):
            self.last_confirmation = {"proposal": str(args.get("proposal", "")), "answer": result.get("answer", "")}
        elif tool == "escalate_case" and isinstance(result, dict) and result.get("ok"):
            self.handed_off = True
        return None
