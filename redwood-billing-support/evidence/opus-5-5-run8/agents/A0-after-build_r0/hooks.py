"""Guardrails around developer messages and tool calls (run for subagents too).

1. Payment card numbers never enter the conversation or a console record (PCI).
2. Console writes need the developer's explicit "yes" via confirm_with_developer.
3. A credit note is only issued for a console-confirmed duplicate line on a direct invoice,
   for the line's full amount, once, and within the per-line limit stated in the current
   official approval documentation (read from the index, not hard-coded).
4. Escalations name a valid team and a summary, and happen once; identical writes are not repeated.
"""
from __future__ import annotations

import json
import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

import retrieval
from session import SESSION

WRITES = ("issue_credit_note", "open_dispute", "escalate_case")
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
    return found


def _rows(table: str, where: dict) -> list[dict]:
    billing = SESSION.billing
    if billing is None:
        return []
    return billing.query(table, where).get("rows", [])


class Hooks(HookKitBase):
    # ---------------------------------------------------------------- developer messages

    @hook("user_message")
    def a_redact_card_numbers(self, text: str) -> str | None:
        """Mask full card numbers and security codes; keep the last four digits Billing Ops needs."""
        cards = _card_numbers(text)
        if not cards and not CVV.search(text or ""):
            return None
        for card in cards:
            digits = re.sub(r"\D", "", card)
            text = text.replace(card, f"[card number removed by Redwood support for security; ends in {digits[-4:]}]")
        return CVV.sub("[security code removed]", text)

    # ---------------------------------------------------------------- every tool call

    @hook("pre_tool")
    def b_no_card_data_in_tools(self, tool: str, args: dict) -> dict | None:
        if _card_numbers(json.dumps(args)):
            raise Blocked("Tool arguments contain a full payment card number. Never record or pass card numbers; "
                          "use only the last four digits.")
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
        return {**args, "team": team}

    @hook("pre_tool", match="*")
    def e_require_developer_consent(self, tool: str, args: dict) -> None:
        if tool not in WRITES:
            return None
        last = SESSION.last_confirmation()
        if last is None:
            raise Blocked("Before this action, call confirm_with_developer with exactly what you will do "
                          "(e.g. which line you will credit and the amount, or which team you will hand the case to).")
        if last["answer"] != "yes":
            raise Blocked("The developer did not approve the last proposal. Do not take that action. If a different "
                          "action is appropriate, propose it with confirm_with_developer first.")
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
        reason = str(args.get("reason", "")).strip()
        if reason != "duplicate":
            raise Blocked("Support issues credit notes only for console-confirmed duplicate charges (reason "
                          "'duplicate'). Goodwill, waivers, SLA/service credits and exceptions are routed to the "
                          "owning team instead; search the routing pages.")
        invoice_id = str(args.get("invoice_id", "")).strip()
        line_id = str(args.get("line_id", "")).strip()
        invoices = _rows("invoices", {"invoice_id": invoice_id})
        if not invoices:
            raise Blocked(f"Invoice {invoice_id!r} not found in the console.")
        invoice = invoices[0]
        if invoice["account_id"] != str(args.get("account_id", "")).strip():
            raise Blocked("The invoice does not belong to that account.")
        accounts = _rows("accounts", {"account_id": invoice["account_id"]})
        if accounts and accounts[0].get("billing_channel") != "direct":
            raise Blocked(f"This account is billed through {accounts[0].get('billing_channel')}; Support issues no "
                          "credit for it. Search the policy for who handles that channel.")
        lines = _rows("invoice_lines", {"invoice_id": invoice_id})
        line = next((x for x in lines if x["line_id"] == line_id), None)
        if line is None:
            raise Blocked(f"Line {line_id!r} is not on invoice {invoice_id}.")
        twins = [x for x in lines if x["line_id"] != line_id and x.get("request_ids")
                 and sorted(x["request_ids"]) == sorted(line.get("request_ids") or [])]
        if not line.get("request_ids") or not twins:
            raise Blocked("The console does not confirm a duplicate: no other line on this invoice bills the same "
                          "request ids. Do not credit; follow the routing for an unconfirmed duplicate.")
        if int(args.get("amount_cents", 0)) != int(line["amount_cents"]):
            raise Blocked(f"A duplicate credit is for the duplicated line's full amount ({line['amount_cents']} "
                          "cents), never a partial or split amount.")
        group = {line_id, *(t["line_id"] for t in twins)}
        for note in _rows("credit_notes", {"invoice_id": invoice_id}):
            if group & set(note.get("line_ids") or []):
                raise Blocked(f"A credit note already exists for this duplicate ({note.get('credit_note_id')}). "
                              "Do not credit it again.")
        limit = retrieval.support_duplicate_credit_limit_cents()
        if limit is not None and int(line["amount_cents"]) > limit:
            raise Blocked(f"This line ({line['amount_cents']} cents) is over Support's per-line duplicate-credit "
                          f"limit in the current approval documentation ({limit} cents). Do not credit or split it; "
                          "follow the over-limit procedure (dispute on the line, then hand off).")
        return None
