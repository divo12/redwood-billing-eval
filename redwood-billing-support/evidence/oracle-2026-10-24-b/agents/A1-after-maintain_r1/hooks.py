import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

# Source: R1/sources/confluence/customer-success-and-support/support-process/payment-card-data-in-support-2027.json.
CARD_REMOVED = "customer shared a card number (removed)"
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_CARD_NUMBER = re.compile(
    rf"\b(?:\d[ -]?){{12,18}}\d\b|\b(?:{_DIGIT_WORD}[\s,-]+){{11,18}}{_DIGIT_WORD}\b",
    re.IGNORECASE,
)


def redact_card_numbers(text: str) -> str:
    return _CARD_NUMBER.sub(CARD_REMOVED, text)


def _redacted(value):
    """`value` with every card number removed from every string inside it."""
    if isinstance(value, str):
        return redact_card_numbers(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


class Hooks(HookKitBase):
    def __init__(self) -> None:
        self._looked_up_accounts: set[str] = set()
        self._policy_texts: list[str] = []
        self._consented = False

    @hook("user_message")
    def remove_card_numbers(self, text):
        redacted = redact_card_numbers(text)
        return redacted if redacted != text else None

    # Source: R1/sources/confluence/customer-success-and-support/support-process/support-quality-standards-2027.json.
    @hook("post_tool", "confirm_with_developer")
    def remember_consent(self, tool, args, result):
        if isinstance(result, dict) and result.get("answer") == "yes":
            self._consented = True

    def _require_confirmation(self):
        if not self._consented:
            raise Blocked(
                "call confirm_with_developer with exactly what you will write (invoice line, and amount "
                "for a credit), and write only after it returns yes"
            )

    @hook("pre_tool", "*")
    def redact_write_arguments(self, tool, args):
        return _redacted(args)

    @hook("post_tool", "find_account")
    def remember_account(self, tool, args, result):
        account = result.get("account")
        if account and account.get("account_id"):
            self._looked_up_accounts.add(account["account_id"])

    # Source: R0/sources/confluence/customer-success-and-support/support-process/billing-case-routing-and-handoffs.json.
    @hook("post_tool", "search_policy")
    def remember_policy(self, tool, args, result):
        for row in result.get("results", []):
            text = row.get("text")
            if isinstance(text, str):
                self._policy_texts.append(" ".join(text.split()))

    def _require_lookup(self, args):
        account_id = args.get("account_id")
        if account_id not in self._looked_up_accounts:
            raise Blocked("look up the account with find_account before writing")

    @hook("pre_tool", "credit_duplicate")
    def before_credit(self, tool, args):
        self._require_lookup(args)
        self._require_confirmation()

    @hook("pre_tool", "open_dispute")
    def before_dispute(self, tool, args):
        self._require_lookup(args)
        self._require_confirmation()

    @hook("pre_tool", "escalate_case")
    def before_escalation(self, tool, args):
        self._require_lookup(args)
        if not str(args.get("unresolved", "")).strip():
            raise Blocked("describe what Support cannot resolve before escalating")
        routing_rule = " ".join(str(args.get("routing_rule", "")).split())
        if len(routing_rule) < 20 or not any(
            routing_rule in text for text in self._policy_texts
        ):
            raise Blocked("quote a routing rule from search_policy before escalating")
        team = " ".join(str(args.get("team", "")).replace("_", " ").split())
        if not team or team.casefold() not in routing_rule.replace("_", " ").casefold():
            raise Blocked("the retrieved routing rule must name the case team")
