"""Deterministic checks of tools, hooks, skills, subagent wiring and the turn loop. No model calls.

    cd /app/workspace && python tests/test_components.py

Uses the release-matched practice console (writes are local) and a scripted model gateway.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

import console  # noqa: E402
import session  # noqa: E402
from hooks import Hooks, mask_payment_data  # noqa: E402
from redwood_kit.agent_context import (ActionInterface, AgentBuildContext, KitResources, ModelConfig,  # noqa: E402
                                       ModelGateway, activate_agent_context)
from redwood_kit.billing import development_billing  # noqa: E402
from redwood_kit.embeddings import encode  # noqa: E402
from redwood_kit.hooks import ToolRunner  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, UserMessage  # noqa: E402
from tools import Tools  # noqa: E402

MODEL = "openai/gpt-6-luna"
EMB = "sentence-transformers/all-MiniLM-L6-v2"


class FakeDeveloper:
    def __init__(self, confirm="yes"):
        self.confirm_answer, self.calls = confirm, []

    def ask(self, fields):
        self.calls.append(("ask", fields))
        return {"answers": {f: "I don't have that." for f in fields}}

    def confirm(self, proposal):
        self.calls.append(("confirm", proposal))
        return {"answer": self.confirm_answer, "message": "ok"}


def make(script=None, confirm="yes"):
    session.reset()
    replies = list(script or [])

    def send(payload):
        if payload["call"] == "embed":
            return {"vectors": encode(payload["model"], payload["inputs"])}
        if payload["call"] == "model":
            msg = replies.pop(0) if replies else {"role": "assistant", "content": "done"}
            return {"message": msg}
        raise AssertionError(payload)

    gateway = ModelGateway((ModelConfig(MODEL),), send, (ModelConfig(EMB),))
    tools, hooks = Tools(), Hooks()
    runner = ToolRunner(tools, hooks)
    billing = development_billing()
    tools.gateway, tools.runner, tools.billing = gateway, runner, billing
    tools.developer = FakeDeveloper(confirm)
    console.attach(billing)
    return tools, runner, gateway, billing


def call(runner, name, **args):
    msg = runner.run(ToolCall(f"c-{name}", name, args))
    body = msg.content
    return msg.error, (json.loads(body) if not msg.error else body)


def find_limit_chunk(runner):
    err, res = call(runner, "search_knowledge",
                    queries=["Billing Adjustments Approval Matrix duplicate-charge credit limit per duplicated line"])
    assert not err, res
    for c in res["results"]:
        if "$500 per" in c["text"] and "superseded" not in c["status"]:
            return c["chunk_id"]
    raise AssertionError([(c["source"], c["status"]) for c in res["results"]])


LIMIT = dict(policy_limit_cents=50000, limit_basis="August 2026 usage is rated on the 2026 price book")


def test_masking():
    t = "my card is 4242 4242 4242 4242 exp 12/29 and key sk_live_abcdefghijklmnop"
    out = mask_payment_data(t)
    assert "4242" not in out and "card number removed" in out and "sk_live" not in out, out
    assert "9876" not in mask_payment_data("my visa ending in 9876 was declined")
    assert "removed" in mask_payment_data("four one one one one one one one one one one one one one one one")
    assert mask_payment_data("invoice INV-2026-0912 for $1,234.56 req 1234567890123") == \
        "invoice INV-2026-0912 for $1,234.56 req 1234567890123"


def test_reads():
    tools, runner, *_ = make()
    err, acct = call(runner, "lookup_account", org="alderwick-labs")
    assert not err and acct["account"]["account_id"] == "practice-alderwick", acct
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")
    assert inv["duplicate_analysis"]["lines_billing_identical_request_ids"][0]["line_ids"] == [
        "PRACTICE-DUP-180/L1", "PRACTICE-DUP-180/L2"], inv
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-LOOKALIKE")
    assert not inv["duplicate_analysis"]["lines_billing_identical_request_ids"], inv
    assert inv["duplicate_analysis"]["same_amount_and_description_lines"], inv
    dup = inv = None
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")
    dup = inv["duplicate_analysis"]["lines_billing_identical_request_ids"][0]
    assert dup["confirmed"] and dup["original"] == "PRACTICE-DUP-180/L1" and dup["copies"] == ["PRACTICE-DUP-180/L2"]
    assert inv["lines"][0]["amount_cents_int"] == 18000, inv["lines"][0]
    err, usage = call(runner, "get_usage", account_id="practice-emberquarry", request_ids=["req_practice_market_01"])
    assert usage["usage"][0]["billed_on_line"].startswith("not billed"), usage
    err, bal = call(runner, "get_credit_balance", account_id="practice-juniper")
    assert not err and bal["source"].startswith("credit_ledger"), bal
    assert bal["grants"][0]["balance_cents"] == 12000 and bal["grants"][0]["expires_on"] == "2027-04-10", bal
    err, acct = call(runner, "lookup_account", account_id="brackenvale-ai")  # org given as id still resolves
    assert not err and acct["contracts"][0]["sla"] is True, acct


def test_credit_guardrails():
    tools, runner, *_ = make()
    good = dict(account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180", line_ids=["PRACTICE-DUP-180/L2"],
                amount_cents=18000, reason="duplicate", memo="L2 duplicates L1 (req_practice_dup_180)")
    err, res = call(runner, "issue_credit_note", **good, policy_chunk_id="x", **LIMIT)
    assert err and "confirm_with_developer" in res, res  # no consent yet
    call(runner, "confirm_with_developer", proposal="Credit $180.00 on PRACTICE-DUP-180/L2 as a duplicate")
    err, res = call(runner, "issue_credit_note", **good, policy_chunk_id="not-retrieved", **LIMIT)
    assert err and "search_knowledge" in res, res
    cid = find_limit_chunk(runner)
    err, res = call(runner, "issue_credit_note", **good, policy_chunk_id=cid, policy_limit_cents=150000,
                    limit_basis=LIMIT["limit_basis"])
    assert err and "does not state" in res, res  # a limit the passage doesn't state ($1,500 is the draft)
    err, res = call(runner, "issue_credit_note", **{**good, "amount_cents": 9000}, policy_chunk_id=cid, **LIMIT)
    assert err, res  # partial credit: consent names $180 not $90
    orig = {**good, "line_ids": ["PRACTICE-DUP-180/L1"]}
    call(runner, "confirm_with_developer", proposal="Credit $180.00 on PRACTICE-DUP-180/L1 as a duplicate")
    err, res = call(runner, "issue_credit_note", **orig, policy_chunk_id=cid, **LIMIT)
    assert err and "original" in res, res  # L1 is the line the usage points to
    call(runner, "confirm_with_developer", proposal="Credit $180.00 on PRACTICE-DUP-180/L2 as a duplicate")
    err, res = call(runner, "issue_credit_note", **good, policy_chunk_id=cid, **LIMIT)
    assert not err and res["credit_note"]["amount"] == {"value": "180.00", "currency": "USD"}, res
    assert res["credit_note"]["billing_account_id"] == "practice-alderwick", res
    err, res = call(runner, "issue_credit_note", **good, policy_chunk_id=cid, **LIMIT)
    assert err and "already" in res, res  # no second credit


def test_contract_account_cannot_be_credited():
    tools, runner, *_ = make()
    cid = find_limit_chunk(runner)
    call(runner, "confirm_with_developer", proposal="Credit $60.00 on PRACTICE-R1-DUP-60/L2")
    err, res = call(runner, "issue_credit_note", account_id="practice-ironbark", invoice_id="PRACTICE-R1-DUP-60",
                    line_ids=["PRACTICE-R1-DUP-60/L2"], amount_cents=6000, reason="duplicate", memo="dup",
                    policy_chunk_id=cid, policy_limit_cents=50000, limit_basis="2027 price book")
    assert err and "self-serve" in res, res


def test_over_limit_and_lookalike():
    tools, runner, *_ = make()
    cid = find_limit_chunk(runner)
    call(runner, "confirm_with_developer", proposal="Credit $900.00 on PRACTICE-DUP-900/L2")
    err, res = call(runner, "issue_credit_note", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                    line_ids=["PRACTICE-DUP-900/L2"], amount_cents=90000, reason="duplicate", memo="dup",
                    policy_chunk_id=cid, **LIMIT)
    assert err and ("exceeds" in res or "self-serve" in res), res
    call(runner, "confirm_with_developer", proposal="Credit $180.00 on PRACTICE-LOOKALIKE/L2")
    err, res = call(runner, "issue_credit_note", account_id="practice-dawnmere", invoice_id="PRACTICE-LOOKALIKE",
                    line_ids=["PRACTICE-LOOKALIKE/L2"], amount_cents=18000, reason="duplicate", memo="dup",
                    policy_chunk_id=cid, **LIMIT)
    assert err and "does not confirm a duplicate" in res, res


def test_dispute_and_escalation():
    tools, runner, *_ = make()
    err, res = call(runner, "open_dispute", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                    line_ids=["PRACTICE-DUP-900/L2"], reason="duplicate_charge", request_ids=["req_practice_dup_900"])
    assert err, res
    call(runner, "confirm_with_developer",
         proposal="Open a dispute on PRACTICE-DUP-900 line L2 and hand it to Billing Ops")
    err, d = call(runner, "open_dispute", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                  line_ids=["PRACTICE-DUP-900/L2"], reason="duplicate_charge", request_ids=["req_practice_dup_900"])
    assert not err and d["dispute"]["status"] == "submitted", d
    err, c = call(runner, "escalate_case", account_id="practice-copperfern", team="billing_ops", priority="normal",
                  summary="Duplicate $900 dedicated line over Support's limit; dispute opened",
                  details={"invoice_id": "PRACTICE-DUP-900", "card": "4242424242424242"},
                  dispute_id=d["dispute"]["dispute_id"])
    assert not err and c["case"]["status"] == "escalated" and c["case"]["priority"] == "normal", c
    masked = Hooks().h50_check_case("escalate_case", {"summary": "Card 4242424242424242 failed on invoice X",
                                                     "details": {"card": "4242 4242 4242 4242"}})
    assert "4242" not in json.dumps(masked) and "card number (removed)" in json.dumps(masked), masked
    err, res = call(runner, "lookup_account", org="copperfern-data")
    assert err and "handed off" in res, res


def test_no_repeat_questions():
    tools, runner, *_ = make()
    err, res = call(runner, "ask_developer", fields=["invoice_id", "org_id"])
    assert not err, res
    err, res = call(runner, "ask_developer", fields=["org_id"])
    assert err and "already asked" in res, res
    err, res = call(runner, "ask_developer", fields=["org_id", "request_ids"])
    assert not err and list(res["answers"]) == ["request_ids"], res


def test_handoff_without_consent():
    tools, runner, *_ = make()
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="finance",
                    summary="Goodwill credit request after outage, $50 asked")
    assert not err, res


def test_declined_consent():
    tools, runner, *_ = make(confirm="no")
    call(runner, "confirm_with_developer", proposal="Hand this to Finance")
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="finance",
                    summary="Goodwill credit request after outage")
    assert err and "no" in res, res


def test_skill_and_subagent_and_turn():
    script = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "name": "load_skill", "arguments": {"name": "invoice-charge-review"}},
            {"id": "t2", "name": "delegate", "arguments": {"subagent": "policy-researcher",
                                                            "task": "What may Support credit for a duplicate?"}}]},
        # subagent: one search, then its answer
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "s1", "name": "search_knowledge", "arguments": {"queries": ["duplicate charge credit limit"]}}]},
        {"role": "assistant", "content": "ANSWER: credit within the matrix limit."},
        {"role": "assistant", "content": "Could you please share the invoice id for that charge?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a1", "name": "ask_developer", "arguments": {"fields": ["invoice_id"]}}]},
    ]
    tools, runner, gateway, billing = make(script)
    ctx = AgentBuildContext(ActionInterface(tuple(tools.get_tools())), KitResources.from_root(WS), gateway,
                            {"company": "Redwood Inference", "role": "billing_support"}, billing)
    with activate_agent_context(ctx):
        import agent as agent_module
        agent = agent_module.create_agent()
    state = agent.get_init_state([AssistantMessage(content="Hi! How can I help you today?")])
    reply, state = agent.generate_next_message(
        UserMessage(content="Our August invoice has the same $180 hosted charge twice, org alderwick-labs"), state)
    assert reply.tool_calls and {c.name for c in reply.tool_calls} == {"load_skill", "delegate"}
    assert "auto_retrieved" in state["messages"][-2].content
    from redwood_kit.messages import MultiToolMessage
    results = MultiToolMessage([runner.run(c) for c in reply.tool_calls])
    assert not any(m.error for m in results.tool_messages), results
    assert "Goal:" in json.loads(results.tool_messages[0].content)["steps"]
    assert "ANSWER" in json.loads(results.tool_messages[1].content)["answer"]
    reply, state = agent.generate_next_message(results, state)
    # chat question got nudged into an ask_developer call
    assert reply.tool_calls and reply.tool_calls[0].name == "ask_developer", reply


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
