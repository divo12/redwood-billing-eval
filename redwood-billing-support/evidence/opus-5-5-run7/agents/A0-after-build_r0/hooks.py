"""Guardrails around every developer message and tool call (including a subagent's).

1. Payment-card data never enters the conversation or a case: card numbers (Luhn-valid 13-19 digits)
   and security codes are redacted from developer messages, and writes containing one are refused.
2. Every console write needs the developer's approval: the most recent confirm_with_developer must
   be "yes". One approval covers one credit note.
3. No writes after a handoff: once a case is escalated the conversation belongs to that team.
4. No blind retry: a write that failed must be followed by a console read before another write,
   because an errored or timed-out insert may still have happened.
5. One credit note per line per conversation (the tool itself also checks the line's balance).
"""
from __future__ import annotations

import json
import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

WRITE_TOOLS = ("issue_credit_note", "open_dispute", "escalate_case")
READ_TOOLS = ("lookup_account", "get_invoice", "get_usage", "console_query")
CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
CVV = re.compile(r"\b(cvv2?|cvc2?|security code)\b\D{0,12}\d{3,4}\b", re.I)
REDACTED = "[card number removed: Support never takes card details in chat]"


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


def redact_cards(text: str) -> tuple[str, bool]:
    found = False

    def swap(match: re.Match) -> str:
        nonlocal found
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found = True
            return REDACTED
        return match.group(0)

    text = CARD_CANDIDATE.sub(swap, text)
    text, n = CVV.subn("[security code removed]", text)
    return text, found or n > 0


class Hooks(HookKitBase):
    def __init__(self) -> None:
        self.approval: str | None = None  # "yes" / "no" from the latest confirm_with_developer
        self.approval_used_for_credit = False
        self.handed_off = False
        self.pending_write: str | None = None  # a write that started but has not reported success
        self.credited_lines: set[str] = set()

    # ---------------------------------------------------------------- developer messages

    @hook("user_message")
    def a_redact_card_data(self, text: str) -> str | None:
        self.approval = None  # consent is per developer turn: it must be asked for again after a new message
        cleaned, changed = redact_cards(text)
        return cleaned if changed else None

    # ---------------------------------------------------------------- before tools

    @hook("pre_tool", match="*")
    def b_no_card_data_in_writes(self, tool: str, args: dict) -> None:
        if tool in WRITE_TOOLS or tool == "confirm_with_developer":
            if redact_cards(json.dumps(args))[1]:
                raise Blocked("Payment-card data must not be written to the console or repeated. "
                              "Remove it; card changes are made by the customer or Billing Ops through secure channels.")

    @hook("pre_tool", match="*")
    def c_writes_need_open_conversation(self, tool: str, args: dict) -> None:
        if tool in WRITE_TOOLS and self.handed_off:
            raise Blocked("This conversation was already handed off to a team; the console is closed. "
                          "Tell the developer what was done and who has the case.")

    @hook("pre_tool", match="*")
    def d_writes_need_approval(self, tool: str, args: dict) -> None:
        if tool not in WRITE_TOOLS:
            return
        if self.approval != "yes":
            raise Blocked("No developer approval for this action. First call confirm_with_developer with "
                          "exactly what you will do (which line/amount, or which team gets the case), then retry.")
        if tool == "issue_credit_note" and self.approval_used_for_credit:
            raise Blocked("That approval was already used for a credit note. Each credit note needs its own approval.")

    @hook("pre_tool", match="*")
    def e_no_blind_retry(self, tool: str, args: dict) -> None:
        if tool in WRITE_TOOLS and self.pending_write:
            raise Blocked(f"A previous {self.pending_write} call failed. Before any other write, read the account "
                          "(lookup_account/get_invoice) to see whether it actually happened; never repeat a write blindly.")

    @hook("pre_tool", match="*")
    def f_credit_once_per_line(self, tool: str, args: dict) -> None:
        if tool == "issue_credit_note" and args.get("line_id") in self.credited_lines:
            raise Blocked(f"A credit note was already issued on {args.get('line_id')} in this conversation.")

    @hook("pre_tool", match="*")
    def z_mark_pending_write(self, tool: str, args: dict) -> None:
        # Runs last: the write is about to execute. post_tool clears it on success; if the tool raises,
        # post_tool never runs and the marker stays, so the next write is held until a read.
        if tool in WRITE_TOOLS:
            self.pending_write = tool

    # ---------------------------------------------------------------- after tools

    @hook("post_tool", match="*")
    def g_track_state(self, tool: str, args: dict, result) -> None:
        if tool == "confirm_with_developer" and isinstance(result, dict):
            self.approval = str(result.get("answer", "")).strip().lower() or None
            self.approval_used_for_credit = False
        elif tool == "issue_credit_note":
            self.approval_used_for_credit = True
            self.credited_lines.add(args.get("line_id"))
        elif tool == "escalate_case":
            self.handed_off = True
        if tool in WRITE_TOOLS or tool in READ_TOOLS:
            self.pending_write = None  # a successful write, or a read that shows the account's real state
        return None
