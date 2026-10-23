"""Guardrails around developer messages and tool calls.

1. Payment card numbers never reach the model, the case notes or the logs (user_message redaction,
   pre_tool scrub of write arguments).
2. Every console write needs a fresh "yes" from confirm_with_developer; a "no" blocks it.
3. A credit note must be backed by console evidence (a confirmed duplicate on a direct invoice, no
   earlier credit on the line, amount within the line's uncredited balance) and by a policy passage
   retrieved in this conversation whose stated limit covers the amount.
4. After a case is escalated the conversation is closed for console writes.
5. A subagent may only call the tools its definition lists (no writes, no nested delegation).
"""
from __future__ import annotations

import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

from ledger import LEDGER

# Credit reasons Support may issue from the console (current policy: duplicates only). Review at each release.
SUPPORT_CREDIT_REASONS = ("duplicate",)
WRITE_TOOLS = ("issue_credit_note", "open_dispute", "escalate_case")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{2})?)")


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact_cards(text: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            count += 1
            return "[card number removed]"
        return match.group(0)

    return CARD.sub(replace, text), count


def _scrub(value):
    if isinstance(value, str):
        return redact_cards(value)[0]
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


class Hooks(HookKitBase):
    # -------------------------------------------------------------- developer messages

    @hook("user_message")
    def a_redact_card_numbers(self, text: str) -> str | None:
        cleaned, count = redact_cards(text)
        if not count:
            return None
        return cleaned + ("\n\n[Note from the support system: a payment card number was removed from this message. "
                          "Never ask for or record card numbers, expiry dates or security codes.]")

    # -------------------------------------------------------------- tool calls

    @hook("post_tool", match="confirm_with_developer")
    def record_consent(self, tool: str, args: dict, result):
        answer = str(result.get("answer", "")).strip().lower() if isinstance(result, dict) else ""
        LEDGER.consents.append((str(args.get("proposal", "")), answer))
        return None

    @hook("pre_tool", match="*")
    def a_subagent_stays_in_scope(self, tool: str, args: dict) -> dict | None:
        if LEDGER.delegating is not None and tool not in LEDGER.delegating:
            raise Blocked(f"{tool} is not available to this subagent. Return your findings as text instead.")
        return None

    @hook("pre_tool", match="*")
    def b_closed_after_handoff(self, tool: str, args: dict) -> dict | None:
        if LEDGER.escalated and tool in (*WRITE_TOOLS, "account_overview", "invoice_details", "usage_lookup",
                                         "console_query"):
            raise Blocked("The case is already escalated; the console is closed for this conversation. "
                          "Tell the developer what was handed off and to whom.")
        return None

    @hook("pre_tool", match="*")
    def c_require_consent(self, tool: str, args: dict) -> dict | None:
        if tool not in WRITE_TOOLS:
            return None
        latest = LEDGER.latest_consent()
        if latest is None:
            raise Blocked("No approval yet. Call confirm_with_developer with exactly what you will do "
                          "(which record, which lines, which amount or team) before this action.")
        index, proposal, answer = latest
        if answer != "yes":
            raise Blocked(f"The developer declined ({proposal!r}). Do not take this action; tell them what "
                          "happens instead, or ask for approval of a different action.")
        if index == LEDGER.used_consent and tool == "issue_credit_note":
            raise Blocked("This approval was already used for an earlier write. Ask again for this credit.")
        return None

    @hook("pre_tool", match="*")
    def d_scrub_write_arguments(self, tool: str, args: dict) -> dict | None:
        if tool in WRITE_TOOLS:
            return _scrub(args)
        return None

    @hook("pre_tool", match="issue_credit_note")
    def e_credit_needs_evidence(self, tool: str, args: dict) -> dict | None:
        toolkit = LEDGER.toolkit
        billing = getattr(toolkit, "billing", None)
        if billing is None:
            raise Blocked("The billing console is not available.")
        invoice_id = str(args.get("invoice_id", ""))
        line_ids = args.get("line_ids") or []
        if isinstance(line_ids, str):
            line_ids = [line_ids]
        try:
            amount = int(args.get("amount_cents", 0))
        except (TypeError, ValueError):
            raise Blocked("amount_cents must be an integer number of cents.") from None
        if amount <= 0 or len(line_ids) != 1:
            raise Blocked("Credit exactly one invoice line with a positive amount.")
        invoices = billing.query("invoices", {"invoice_id": invoice_id}).get("rows", [])
        if not invoices or invoices[0].get("account_id") != args.get("account_id"):
            raise Blocked("That invoice is not on this account in the console.")
        accounts = billing.query("accounts", {"account_id": invoices[0]["account_id"]}).get("rows", [])
        if not accounts or accounts[0].get("billing_channel") != "direct":
            raise Blocked("This account is not billed directly by Redwood; Support cannot credit its invoices.")
        lines = billing.query("invoice_lines", {"invoice_id": invoice_id}).get("rows", [])
        by_id = {line["line_id"]: line for line in lines}
        target = by_id.get(line_ids[0])
        if target is None:
            raise Blocked("That line is not on this invoice.")
        if args.get("reason") not in SUPPORT_CREDIT_REASONS:
            raise Blocked("Support issues console credits only for console-confirmed duplicate charges. Goodwill, "
                          "SLA/service credits and exceptions are decided by the owning team: route the request "
                          "per the routing policy instead of crediting.")
        key = sorted(target.get("request_ids") or [])
        twins = [l for l in lines if l["line_id"] != target["line_id"] and sorted(l.get("request_ids") or []) == key]
        if not key or not twins:
            raise Blocked("The console does not confirm this line as a duplicate (no other line on the "
                          "invoice bills the same request ids). Do not credit it; follow the routing policy.")
        group = {target["line_id"], *(l["line_id"] for l in twins)}
        notes = billing.query("credit_notes", {"invoice_id": invoice_id}).get("rows", [])
        if any(group & set(n.get("line_ids") or []) for n in notes):
            raise Blocked("This duplicate already has a credit note on one of its lines. Do not credit it again.")
        if amount > int(target.get("amount_cents", 0)):
            raise Blocked("The amount exceeds the line's amount.")
        # The policy passage that authorises Support must have been retrieved here and state a limit
        # that covers this amount.
        source = str(args.get("policy_source", "")).strip().strip("[]")
        limit_text = str(args.get("policy_limit", ""))
        retrieved = LEDGER.retrieved.get(source)
        if not retrieved:
            raise Blocked("policy_source must be the source id of a policy passage returned by search_knowledge "
                          "in this conversation that sets Support's credit limit. Search for it first.")
        figures = [m.replace(",", "") for m in MONEY.findall(limit_text)]
        if not figures or not any(f"${f}" in retrieved.replace(",", "") or f"${f.split('.')[0]}" in
                                  retrieved.replace(",", "") for f in figures):
            raise Blocked("policy_limit must quote the dollar limit as it appears in the policy_source passage.")
        limit_cents = int(round(float(figures[0]) * 100))
        if amount > limit_cents:
            raise Blocked(f"{amount} cents is over the limit you cited ({limit_text}). Support must not issue "
                          "this credit; follow the policy for amounts over the limit.")
        LEDGER.used_consent = len(LEDGER.consents) - 1
        return None

    @hook("pre_tool", match="escalate_case")
    def f_case_has_summary(self, tool: str, args: dict) -> dict | None:
        if not str(args.get("summary", "")).strip() or not str(args.get("team", "")).strip():
            raise Blocked("An escalated case needs a team and a summary of the request and findings.")
        return None
