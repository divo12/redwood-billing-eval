import re

from redwood_kit.hooks import Blocked, HookKitBase, hook

# Source: R1/sources/confluence/customer-success-and-support/support-process/payment-card-data-in-support-2027.json.
CARD_REMOVED = "customer shared a card number (removed)"
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_CARD_NUMBER = re.compile(
    rf"\b(?:\d[ -]?){{12,18}}\d\b|\b(?:{_DIGIT_WORD}[\s,-]+){{11,18}}{_DIGIT_WORD}\b",
    re.IGNORECASE,
)
# Source: R1/sources/confluence/customer-success-and-support/support-process/support-quality-standards-2027.json.
_YES = re.compile(r"\b(yes|yeah|yep|confirm(?:ed)?|go ahead|proceed|do it|please do|sure|ok(?:ay)?)\b", re.IGNORECASE)
_NO = re.compile(r"\b(no|not|don't|do not|wait|hold off|cancel)\b", re.IGNORECASE)
_LEADING_YES = re.compile(r"^\W*(yes|yeah|yep|confirm(?:ed)?|go ahead|proceed|sure|ok(?:ay)?)\b", re.IGNORECASE)


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
        self._customer_messages: list[str] = []

    @hook("user_message")
    def remove_card_numbers(self, text):
        redacted = redact_card_numbers(text)
        self._customer_messages.append(redacted)
        return redacted if redacted != text else None

    def _require_confirmation(self):
        latest = self._customer_messages[-1] if len(self._customer_messages) > 1 else ""
        if not (_LEADING_YES.search(latest) or (_YES.search(latest) and not _NO.search(latest))):
            raise Blocked(
                "tell the customer exactly what you will write (invoice line, and amount for a credit), "
                "ask them to confirm, and write only after they explicitly say yes"
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
