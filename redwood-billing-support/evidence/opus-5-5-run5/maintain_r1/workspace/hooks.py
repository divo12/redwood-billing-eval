"""Guardrails around every developer message and tool call (including a subagent's).

1. a_mask_card_numbers (user_message): card numbers (digits, or digits spelled as words) never enter
   the model context; nothing of the number, not even the last four digits, is kept.
2. b_console_closed (pre_tool): after a handoff the console is closed; refuse further console calls.
3. c_require_consent (pre_tool, writes): a credit or dispute needs the developer's latest
   confirm_with_developer answer to be "yes" to an action naming that line (and amount for a credit).
   After a "no", nothing is written for it — no credit, dispute or case.
4. d_verify_duplicate_credit (pre_tool, issue_duplicate_credit): direct, self-serve, no signed
   contract; the console must show the duplicate and the line must be the copy (the line no usage
   record points to); full line amount; nothing credited yet; within Support's published limit for
   the invoice's price book (read from the index at runtime).
5. e_check_case / e_check_dispute (pre_tool): valid team/reason/priority, no card data, one handoff.
6. z_track (post_tool): remembers confirmations and handoffs.
"""
from __future__ import annotations

import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

import console as C

CREDIT_DISPUTE = ("issue_duplicate_credit", "open_dispute")
WRITE_TOOLS = CREDIT_DISPUTE + ("escalate_case",)
CONSOLE_TOOLS = ("lookup_account", "get_invoice", "get_usage", "get_account_activity",
                 "get_credit_balance") + WRITE_TOOLS
TEAMS = {"billing_ops", "billing_engineering", "finance", "legal", "account_team", "partnerships",
         "telemetry", "sre", "platform"}
DISPUTE_REASONS = {"duplicate_charge", "incorrect_amount", "unrecognized_usage"}
PRIORITIES = {"normal", "urgent"}
CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_DIGIT_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
SPOKEN_CARD = re.compile(rf"\b{_DIGIT_WORDS}(?:[\s,-]+{_DIGIT_WORDS}){{11,}}\b", re.I)
REMOVED = "[card number removed]"


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
        return REMOVED if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)
    return SPOKEN_CARD.sub(REMOVED, CARD.sub(repl, text))


def _money_forms(cents: int) -> list[str]:
    d = cents / 100
    forms = {f"{d:,.2f}", f"{d:.2f}"}
    if cents % 100 == 0:
        forms |= {f"${d:,.0f}", f"${d:.0f}"}
    return sorted(forms)


def _names_line(proposal: str, line_id: str) -> bool:
    tail = line_id.rsplit("/", 1)[-1]
    return bool(line_id) and (line_id in proposal or re.search(rf"\b{re.escape(tail)}\b", proposal) is not None)


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
        if masked == text:
            return None
        return masked + ("\n[note from support tooling: the customer shared a card number; it was removed. "
                         "Do not repeat any part of it.]")

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
        declined = bool(c) and str(c.get("answer", "")).strip().lower() != "yes"
        if tool == "escalate_case":
            if declined and not args.get("dispute_id"):
                raise Blocked("The developer declined your last proposal. Write nothing for it (no credit, dispute "
                              "or case); tell them nothing was changed and they can come back if they change "
                              "their mind. If they asked for something different, confirm that first.")
            return None
        if not c:
            raise Blocked("Before a credit note or dispute, call confirm_with_developer saying exactly what you will "
                          "do: the invoice line and, for a credit, the amount.")
        if declined:
            raise Blocked("The developer did not approve this. Do not write it; tell them nothing has been changed.")
        proposal = c.get("proposal", "")
        lines = [args.get("line_id", "")] if tool == "issue_duplicate_credit" else list(args.get("line_ids") or [])
        if not any(_names_line(proposal, l) for l in lines if l):
            raise Blocked("The approval you got does not name this line. Confirm the exact line (and amount) first.")
        if tool == "issue_duplicate_credit":
            try:
                cents = C.to_cents(args.get("amount"))
            except ValueError:
                raise Blocked("amount must be a dollar amount like \"60.00\"") from None
            if not any(f in proposal for f in _money_forms(cents or 0)):
                raise Blocked("The approval you got does not state this credit amount. Confirm line and amount first.")
        return None

    # 4 ---------------------------------------------------------------------------------------
    @hook("pre_tool", match="issue_duplicate_credit")
    def d_verify_duplicate_credit(self, tool: str, args: dict) -> dict | None:
        billing = self._billing()
        account_id, invoice_id, line_id = (str(args.get(k, "")).strip() for k in ("account_id", "invoice_id", "line_id"))
        try:
            cents = C.to_cents(args.get("amount"))
        except ValueError:
            raise Blocked("amount must be a dollar amount like \"60.00\"") from None
        accounts = C.query_all(billing, "accounts", {"account_id": account_id})
        if not accounts:
            raise Blocked(f"No account {account_id!r}.")
        acct = accounts[0]
        if acct.get("billing_channel") != "direct":
            raise Blocked("This account is billed through a marketplace or reseller; Support issues no credit note. "
                          "Explain who handles their bill instead.")
        contracts = C.query_all(billing, "contracts", {"account_id": account_id})
        if acct.get("plan") != "self_serve" or contracts:
            raise Blocked("Support issues duplicate-charge credit notes only on self-serve accounts with no signed "
                          "contract. For a confirmed duplicate here, open a dispute on the copy line (reason "
                          "duplicate_charge, with the request ids) and hand the case to Billing Ops with the dispute id.")
        invoices = C.query_all(billing, "invoices", {"invoice_id": invoice_id})
        if not invoices or C.row_account(invoices[0]) != account_id:
            raise Blocked("That invoice does not belong to this account.")
        lines = C.query_all(billing, "invoice_lines", {"invoice_id": invoice_id})
        line = next((l for l in lines if l["line_id"] == line_id), None)
        if line is None:
            raise Blocked("That line is not on this invoice.")
        group = next((g for g in C.duplicate_analysis(billing, lines) if line_id in g["lines"]), None)
        if group is None:
            raise Blocked("The console does not confirm a duplicate: no other line on this invoice bills the same "
                          "request ids. Do not credit; follow the routing for unconfirmed duplicates.")
        if not group["console_confirms_duplicate"]:
            raise Blocked(f"The console does not confirm which line is the copy ({group.get('why')}). Do not credit; "
                          "hand it to Billing Ops as an unconfirmed duplicate.")
        if line_id not in group["copy_lines"]:
            raise Blocked(f"{line_id} is the original (the usage records point to it). Credit or dispute only the copy: "
                          f"{', '.join(group['copy_lines'])}.")
        line_cents = C.read_money(line, "amount")
        if cents != line_cents:
            raise Blocked(f"A duplicate credit is for the copy line's full amount ({C.usd(line_cents)}), never a "
                          f"partial or split amount.")
        notes = C.query_all(billing, "credit_notes", {"invoice_id": invoice_id})
        if any(set(group["lines"]) & set(n.get("line_ids") or []) for n in notes):
            raise Blocked("A credit note already exists for this duplicate; do not issue another. Re-read the invoice.")
        from knowledge import duplicate_credit_limit_cents
        limit, source = duplicate_credit_limit_cents(invoices[0].get("period_start"))
        if limit is not None and cents > limit:
            raise Blocked(f"This line ({C.usd(cents)}) is over Support's duplicate-credit limit for this invoice "
                          f"({C.usd(limit)} per line; {source}). Do not credit; open a dispute on the copy line and "
                          f"hand the case to Billing Ops with the dispute id.")
        return None

    # 5 ---------------------------------------------------------------------------------------
    @hook("pre_tool", match="escalate_case")
    def e_check_case(self, tool: str, args: dict) -> dict | None:
        if args.get("team") not in TEAMS:
            raise Blocked(f"team must be one of {sorted(TEAMS)}")
        if not str(args.get("summary", "")).strip():
            raise Blocked("A handoff needs a summary of the ask and what you found.")
        priority = args.get("priority") or "normal"
        if priority not in PRIORITIES:
            raise Blocked("priority must be normal or urgent")

        def clean(v: str) -> str:
            masked = mask_cards(v)
            return masked.replace(REMOVED, "customer shared a card number (removed)")
        details = {str(k): clean(str(v)) for k, v in (args.get("details") or {}).items()}
        return {**args, "summary": clean(str(args["summary"])), "details": details, "priority": priority}

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
