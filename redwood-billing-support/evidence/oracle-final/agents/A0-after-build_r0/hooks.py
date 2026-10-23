from redwood_kit.hooks import Blocked, HookKitBase, hook


class Hooks(HookKitBase):
    def __init__(self) -> None:
        self._looked_up_accounts: set[str] = set()
        self._policy_texts: list[str] = []

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

    @hook("pre_tool", "open_dispute")
    def before_dispute(self, tool, args):
        self._require_lookup(args)

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
