"""Guardrails around developer messages and tool calls (run for subagents too).

1. Payment card numbers never enter the conversation or a console record, not even partially
   (Payment Card Data Handling Standard): masked in developer messages, blocked in tool arguments.
2. Credit notes and disputes need the developer's explicit "yes" via confirm_with_developer;
   after a "no", nothing is written for it, not even a case.
3. A credit note is only issued on a self-serve account (Support charter), for a console-confirmed
   duplicate on a direct invoice, against the copy line (not the original), for the line's full
   amount, once, and within the per-line limit the current approval matrix sets for that invoice
   (read from the index, not hard-coded).
4. Escalations name a valid team, a summary and the standard priority, and happen once.
"""
from __future__ import annotations

import json
import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

from console import Console, cents, dollars, duplicate_limit_cents
from session import SESSION

CONSENT_WRITES = ("issue_credit_note", "open_dispute")
TEAMS = ("billing_ops", "billing_engineering", "finance", "legal", "account_team", "partnerships",
         "telemetry", "sre", "platform")
TEAM_ALIASES = {
    "billing ops": "billing_ops", "billingops": "billing_ops", "billing operations": "billing_ops",
    "billing engineering": "billing_engineering", "billing eng": "billing_engineering",
    "fp&a": "finance", "finance (fp&a)": "finance", "account team": "account_team", "csm": "account_team",
    "tam": "account_team", "partnership": "partnerships",
}
CARD_CANDIDATE = re.compile(r"(?<!\w)(?:\d[ -]?){12,18}\d(?!\w)")
CVV = re.compile(r"\b(cvv|cvc|cvv2|security code)\s*[:#]?\s*\d{3,4}\b", re.I)
DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
               "six": "6", "seven": "7", "eight": "8", "nine": "9"}
SPOKEN_RUN = re.compile(r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)[\s,-]+){11,18}"
                        r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b", re.I)
CARD_NOTE = "[card number removed - Redwood Support never takes card details]"


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_numbers(text: str) -> list[str]:
    found = []
    for m in CARD_CANDIDATE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(m.group(0))
    for m in SPOKEN_RUN.finditer(text or ""):
        found.append(m.group(0))
    return found


def _console() -> Console | None:
    return Console(SESSION.billing) if SESSION.billing is not None else None


class Hooks(HookKitBase):
    # ---------------------------------------------------------------- developer messages

    @hook("user_message")
    def a_redact_card_numbers(self, text: str) -> str | None:
        """Mask card numbers (digits or spoken) and security codes entirely; no part of them is kept."""
        cards = _card_numbers(text)
        if not cards and not CVV.search(text or ""):
            return None
        for card in cards:
            text = text.replace(card, CARD_NOTE)
        return CVV.sub("[security code removed]", text)

    # ---------------------------------------------------------------- every tool call

    @hook("pre_tool")
    def b_no_card_data_in_tools(self, tool: str, args: dict) -> dict | None:
        if _card_numbers(json.dumps(args)):
            raise Blocked("Tool arguments contain a payment card number. Never record or pass any part of a card "
                          "number; write 'customer shared a card number (removed)' if it must be mentioned.")
        return None

    @hook("post_tool", match="confirm_with_developer")
    def c_record_confirmation(self, tool: str, args: dict, result) -> None:
        answer = str((result or {}).get("answer", "")).strip().lower() if isinstance(result, dict) else ""
        SESSION.confirmations.append({"proposal": args.get("proposal", ""), "answer": answer})
        return None

    # ---------------------------------------------------------------- console writes

    @hook("pre_tool", match="escalate_case")
    def d_normalize_escalation(self, tool: str, args: dict) -> dict | None:
        team = str(args.get("team", "")).strip().lower()
        team = TEAM_ALIASES.get(team, team.replace(" ", "_").replace("-", "_"))
        if team not in TEAMS:
            raise Blocked(f"Unknown team {args.get('team')!r}. Use one of: {', '.join(TEAMS)}. "
                          "Check the routing evidence for which team owns this request.")
        if not str(args.get("summary", "")).strip():
            raise Blocked("An escalated case needs a summary of the ask and what you found.")
        if SESSION.handed_off:
            raise Blocked("This conversation was already handed off; the console is closed. Tell the developer "
                          "which team has the case.")
        args = {**args, "team": team}
        if not str(args.get("dispute_id") or "").strip():
            # link the dispute opened earlier in this conversation for the same account
            for done in reversed(SESSION.writes):
                if done["tool"] == "open_dispute" and str(args.get("account_id", "")).strip() in json.dumps(done["row"]):
                    dispute_id = (done["result"] or {}).get("dispute_id")
                    if dispute_id:
                        args["dispute_id"] = dispute_id
                    break
        return args

    @hook("pre_tool", match="*")
    def e_require_developer_consent(self, tool: str, args: dict) -> None:
        last = SESSION.last_confirmation()
        if tool in CONSENT_WRITES:
            if last is None:
                raise Blocked("Before a credit note or dispute, call confirm_with_developer naming the action, the "
                              "invoice line and, for a credit, the amount. Write only after an explicit yes.")
            if last["answer"] != "yes":
                raise Blocked("The developer did not approve. Write nothing for it (no credit note, dispute or case); "
                              "tell them nothing was changed and they can come back if they change their mind.")
        if tool == "escalate_case" and last is not None and last["answer"] != "yes":
            raise Blocked("The developer declined the last proposal, so nothing is written for it, not even a case. "
                          "Tell them nothing was changed.")
        return None

    @hook("pre_tool", match="*")
    def f_no_repeated_dispute(self, tool: str, args: dict) -> None:
        if tool != "open_dispute":
            return None
        wanted = (str(args.get("invoice_id", "")).strip(), sorted(str(x).strip() for x in args.get("line_ids") or []))
        for done in SESSION.writes:
            row = done["row"]
            if done["tool"] == tool and (row.get("invoice_id"), sorted(row.get("line_ids") or [])) == wanted:
                raise Blocked("A dispute on these lines already exists in this conversation "
                              f"({json.dumps(done['result'], default=str)[:300]}). Use its dispute_id; do not repeat it.")
        return None

    @hook("pre_tool", match="issue_credit_note")
    def g_credit_note_is_supported(self, tool: str, args: dict) -> None:
        con = _console()
        if con is None:
            return None
        reason = str(args.get("reason", "")).strip()
        if reason != "duplicate":
            raise Blocked("Support issues credit notes only for console-confirmed duplicate charges (reason "
                          "'duplicate'). Goodwill, waivers, SLA/service credits, refunds and exceptions are routed to "
                          "the owning team instead; search the routing pages.")
        invoice_id = str(args.get("invoice_id", "")).strip()
        line_id = str(args.get("line_id", "")).strip()
        invoices = con.rows("invoices", {"invoice_id": invoice_id})
        if not invoices:
            raise Blocked(f"Invoice {invoice_id!r} not found in the console.")
        invoice = invoices[0]
        if invoice["account_id"] != str(args.get("account_id", "")).strip():
            raise Blocked("The invoice does not belong to that account.")
        account = con.account(invoice["account_id"]) or {}
        if account.get("billing_channel", "direct") != "direct":
            raise Blocked(f"This account is billed through {account.get('billing_channel')}; Support issues no "
                          "credit for it. Search the policy for who handles that channel.")
        contracts = con.rows("contracts", {"account_id": invoice["account_id"]})
        if contracts or account.get("plan") not in (None, "self_serve"):
            raise Blocked("Support may issue credit notes only on self-serve accounts; this account has a signed "
                          "contract / is not self-serve (Support Charter). For a confirmed duplicate, open a dispute "
                          "on the duplicated line (with developer approval) and hand it to Billing Ops.")
        analysis = con.duplicate_analysis(invoice_id)
        group = next((g for g in analysis["groups"] if line_id in g["line_ids"]), None)
        if group is None or not group["console_confirms_duplicate"]:
            raise Blocked("The console does not confirm a duplicate for this line (no other line bills the same "
                          "request ids, or the usage records point to neither/both lines). Do not credit; hand off "
                          "as an unconfirmed duplicate per the routing page.")
        if line_id == group["original_line"]:
            raise Blocked(f"{line_id} is the original (the usage records point to it). Credit only the copy: "
                          f"{', '.join(group['duplicated_lines_to_credit_or_dispute'])}.")
        line = next(x for x in analysis["lines"] if x["line_id"] == line_id)
        line_cents = con.row_cents(line)
        asked = cents(str(args.get("amount", args.get("amount_cents", 0))))
        if asked != line_cents:
            raise Blocked(f"A duplicate credit is for the duplicated line's full amount ({dollars(line_cents)}), "
                          "never a partial or split amount.")
        for note in con.rows("credit_notes", {"invoice_id": invoice_id}):
            if set(group["line_ids"]) & set(note.get("line_ids") or []):
                raise Blocked(f"A credit note already exists for this duplicate ({note.get('credit_note_id')}). "
                              "Do not credit it again.")
        limit, basis = duplicate_limit_cents(invoice)
        if limit is not None and line_cents > limit:
            raise Blocked(f"This line ({dollars(line_cents)}) is over Support's per-line duplicate-credit limit for "
                          f"this invoice ({dollars(limit)}; {basis}). Do not credit or split it: with approval, open a "
                          "dispute on the copy line, then hand the case to Billing Ops.")
        return None

    @hook("pre_tool", match="open_dispute")
    def h_dispute_targets_copy(self, tool: str, args: dict) -> None:
        con = _console()
        if con is None or str(args.get("reason", "")).strip() != "duplicate_charge":
            return None
        invoice_id = str(args.get("invoice_id", "")).strip()
        for group in con.duplicate_analysis(invoice_id)["groups"]:
            if group["console_confirms_duplicate"] and group["original_line"] in (args.get("line_ids") or []):
                raise Blocked(f"{group['original_line']} is the original (the usage records point to it). Dispute "
                              f"only the copy: {', '.join(group['duplicated_lines_to_credit_or_dispute'])}.")
        return None
