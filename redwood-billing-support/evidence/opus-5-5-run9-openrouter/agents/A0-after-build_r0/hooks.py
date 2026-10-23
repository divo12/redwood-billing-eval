"""Guardrails around developer messages and tool calls (they also cover subagent tool calls).

Order matters within an event (method-name order), hence the numeric prefixes.

- user_message  h10_mask_payment_secrets: card numbers and API secret keys never reach the model,
                the console or a case; the last four digits are kept so the fact stays usable.
- pre/post_tool h15/h16 ask_developer: a field already answered is not asked again (the developer's
                earlier answer is returned instead), so turns are not spent on repeat questions.
- post_tool     h20_record_consent: every confirm_with_developer answer is logged in the session.
- pre_tool      h30_require_consent: no console write without a "yes" to a proposal that names it.
- pre_tool      h40_check_credit_note: a credit note must be backed by console facts (lines exist on
                that invoice and account, a "duplicate" really bills the same request ids twice, the
                whole uncredited line, not already credited) and by a retrieved current policy passage
                whose stated limit covers the amount. The limit itself is read from that passage at
                conversation time, never hard-coded here.
- pre_tool      h50_check_case: an escalation needs a real summary and carries no card data.
"""
from __future__ import annotations

import re

import console
import session
from redwood_kit.hooks import Blocked, HookKitBase, hook

WRITE_TOOLS = ("issue_credit_note", "open_dispute", "escalate_case")
PAN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
SECRET = re.compile(r"\b(?:sk|rk|rw)[-_](?:live|test|prod)?[-_]?[A-Za-z0-9]{12,}\b")
NOT_CURRENT = ("draft", "proposal", "archived", "in_review", "superseded", "deprecated")


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


def mask_payment_data(text: str) -> str:
    def card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return f"[card number removed, ends {digits[-4:]}]"
        return match.group(0)

    return SECRET.sub("[secret key removed]", PAN.sub(card, text))


def _amount_forms(cents: int) -> list[str]:
    whole, part = divmod(abs(int(cents)), 100)
    forms = [f"{whole:,}.{part:02d}", f"{whole}.{part:02d}"]
    if part == 0:
        forms += [f"${whole:,}", f"${whole}", f"{whole:,} ", f"{whole} ", f"{whole}"]
    return forms


def _mentions_amount(text: str, cents: int) -> bool:
    plain = text.replace("USD", "$")
    return any(form in plain for form in _amount_forms(cents))


def _states_limit(text: str, cents: int) -> bool:
    whole, part = divmod(abs(int(cents)), 100)
    pattern = rf"\$\s?{whole:,}(?:\.{part:02d})?\b|\$\s?{whole}(?:\.{part:02d})?\b" if part == 0 else \
        rf"\$\s?{whole:,}\.{part:02d}\b|\$\s?{whole}\.{part:02d}\b"
    return re.search(pattern.replace(",", ",?"), text) is not None


class Hooks(HookKitBase):
    # -------------------------------------------------------------- developer messages

    @hook("user_message")
    def h10_mask_payment_secrets(self, text: str) -> str | None:
        masked = mask_payment_data(text)
        return masked if masked != text else None

    # -------------------------------------------------------------- questions to the developer

    @hook("pre_tool", match="ask_developer")
    def h15_no_repeat_questions(self, tool: str, args: dict) -> dict | None:
        fields = [str(f) for f in (args.get("fields") or [])]
        known = session.current().answers
        new = [f for f in fields if f not in known]
        if fields and not new:
            raise Blocked("You already asked for these and the developer answered: "
                          f"{ {f: known[f] for f in fields} }. Work with these answers; do not ask again.")
        return {**args, "fields": new} if new != fields else None

    @hook("post_tool", match="ask_developer")
    def h16_record_answers(self, tool: str, args: dict, result):
        if isinstance(result, dict):
            for name, answer in (result.get("answers") or {}).items():
                session.current().answers[str(name)] = str(answer)
        return None

    # -------------------------------------------------------------- consent

    @hook("post_tool", match="confirm_with_developer")
    def h20_record_consent(self, tool: str, args: dict, result):
        answer = str((result or {}).get("answer", "")).strip().lower() if isinstance(result, dict) else ""
        session.current().consents.append({
            "proposal": str(args.get("proposal", "")),
            "answer": answer,
            "message": str((result or {}).get("message", "")) if isinstance(result, dict) else "",
        })
        if answer != "yes":
            return {**result, "note": "The developer declined. Do not take this action; say what you will not "
                                      "do and offer the alternative the policy allows."}
        return None

    @hook("pre_tool", match="*")
    def h30_require_consent(self, tool: str, args: dict) -> dict | None:
        if tool not in WRITE_TOOLS:
            return None
        state = session.current()
        if state.handed_off:
            raise Blocked("This conversation was already handed off; no further console writes are possible.")
        last = state.last_consent()
        if last is None:
            raise Blocked("Ask the developer first: call confirm_with_developer with exactly what you will do "
                          "(which invoice line, which amount or which team), then retry if they say yes.")
        if last["answer"] != "yes":
            raise Blocked("The developer's latest answer to your proposal was no. Do not write; explain the "
                          "alternative, or propose a different action with confirm_with_developer.")
        proposal = last["proposal"]
        if tool == "issue_credit_note":
            if not _mentions_amount(proposal, int(args.get("amount_cents") or 0)):
                raise Blocked("The approved proposal does not state this credit amount. Confirm the exact amount "
                              "and line with the developer before issuing it.")
            lines = args.get("line_ids") or []
            if not (any(str(l) in proposal for l in lines) or str(args.get("invoice_id", "")) in proposal):
                raise Blocked("The approved proposal does not name this invoice or line. Confirm the exact "
                              "invoice line with the developer before issuing the credit.")
        if tool == "open_dispute" and str(args.get("invoice_id", "")) not in proposal:
            raise Blocked("The approved proposal does not name this invoice. Confirm the dispute (invoice and "
                          "line) with the developer first.")
        return None

    # -------------------------------------------------------------- credit notes

    @hook("pre_tool", match="issue_credit_note")
    def h40_check_credit_note(self, tool: str, args: dict) -> dict | None:
        state = session.current()
        line_ids = [str(x) for x in (args.get("line_ids") or [])]
        amount = int(args.get("amount_cents") or 0)
        if len(line_ids) != 1:
            raise Blocked("Credit exactly one line per credit note.")
        if amount <= 0:
            raise Blocked("amount_cents must be positive.")
        if state.credit_attempts & set(line_ids):
            raise Blocked("A credit note was already attempted for this line in this conversation. Never retry a "
                          "write: query credit_notes for the invoice to see whether it was issued.")

        # Evidence: the cited passage was retrieved here, is current, and states a limit covering the amount.
        cited = state.retrieved.get(str(args.get("policy_chunk_id", "")))
        if cited is None:
            raise Blocked("policy_chunk_id must be the chunk_id of a passage search_knowledge returned in this "
                          "conversation that states Support's limit for this credit. Search for it first.")
        status = cited["status"].lower()
        if any(flag in status for flag in NOT_CURRENT):
            raise Blocked(f"The cited passage ({cited['source']}) is {cited['status']}, not current policy. Cite "
                          "the current published or final source.")
        limit = int(args.get("policy_limit_cents") or 0)
        if limit <= 0 or not _states_limit(cited["text"], limit):
            raise Blocked("The cited passage does not state the limit you passed. Pass the limit exactly as the "
                          "current policy passage states it, or route the request instead of crediting.")
        if amount > limit:
            raise Blocked(f"The amount exceeds the policy limit you cited ({limit / 100:,.2f}). Support does not "
                          "credit it; follow the over-limit path in the policy (dispute and handoff).")

        # Console facts.
        cons = console.billing()
        if cons is None:
            raise Blocked("The billing console is not available to verify this credit; do not issue it.")
        invoice_id = str(args.get("invoice_id", ""))
        account_id = str(args.get("account_id", ""))
        accounts = cons.query("accounts", {"account_id": account_id}).get("rows", [])
        if not accounts:
            raise Blocked(f"No console account {account_id!r}.")
        if accounts[0].get("billing_channel") != "direct":
            raise Blocked("This account is billed through a marketplace, not on a direct Redwood invoice; Support "
                          "issues no credit note for it.")
        invoices = cons.query("invoices", {"invoice_id": invoice_id}).get("rows", [])
        if not invoices or invoices[0].get("account_id") != account_id:
            raise Blocked("That invoice does not belong to this account.")
        lines = console.rows(cons, "invoice_lines", {"invoice_id": invoice_id})
        by_id = {l["line_id"]: l for l in lines}
        line = by_id.get(line_ids[0])
        if line is None:
            raise Blocked("That line is not on this invoice.")
        credited, _ = console.credited_by_line(cons, invoice_id)
        uncredited = line["amount_cents"] - credited.get(line["line_id"], 0)
        if credited.get(line["line_id"]):
            raise Blocked("This line already has a credit note. Do not credit it again; tell the developer what "
                          "the console shows.")
        if args.get("reason") == "duplicate":
            twins = [g for g in console.duplicate_groups(lines) if any(l["line_id"] == line["line_id"] for l in g)]
            if not twins:
                raise Blocked("The console does not confirm a duplicate: no other line on this invoice bills the "
                              "same request ids. Do not credit; follow the unconfirmed-duplicate path.")
            if amount != uncredited:
                raise Blocked(f"A duplicate credit is for the whole duplicated line ({uncredited / 100:,.2f}), "
                              "never a part or a split.")
        elif amount > uncredited:
            raise Blocked("The amount exceeds the line's uncredited balance.")
        return None

    # -------------------------------------------------------------- escalations

    @hook("pre_tool", match="escalate_case")
    def h50_check_case(self, tool: str, args: dict) -> dict | None:
        summary = str(args.get("summary") or "").strip()
        if len(summary) < 15:
            raise Blocked("Write a real summary: what the developer asks for, on which invoice/line or request, "
                          "and why it needs this team.")
        details = {str(k): mask_payment_data(str(v)) for k, v in (args.get("details") or {}).items()}
        return {**args, "summary": mask_payment_data(summary), "details": details}
