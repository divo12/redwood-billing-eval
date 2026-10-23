"""Offline wiring checks: real tools, hooks, skills, subagent and practice console; scripted model, no API calls.

Run from the workspace: python build/test_wiring.py
"""
import json
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WS))

from redwood_kit.agent_context import ActionInterface, AgentBuildContext, KitResources, ModelConfig, ModelGateway, activate_agent_context
from redwood_kit.billing import development_billing
from redwood_kit.embeddings import encode
from redwood_kit.hooks import ToolRunner
from redwood_kit.messages import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage, to_dict

import hooks as hooks_mod
import tools as tools_mod


class FakeDeveloper:
    def __init__(self, confirm="yes"):
        self.confirm_answer = confirm
        self.calls = []

    def ask(self, fields):
        self.calls.append(("ask", fields))
        return {"answers": {f: "I don't have that." for f in fields}}

    def confirm(self, proposal):
        self.calls.append(("confirm", proposal))
        return {"answer": self.confirm_answer, "message": "ok"}


def make(script=None, confirm="yes"):
    """A toolkit + runner wired like the runtime; `script` is a list of AssistantMessages the fake model returns."""
    script = list(script or [])

    def send(payload):
        if payload["call"] == "embed":
            return {"vectors": encode(payload["model"], payload["inputs"])}
        if payload["call"] == "model":
            return {"message": to_dict(script.pop(0))}
        raise AssertionError(payload)

    gateway = ModelGateway((ModelConfig("openai/gpt-6-luna"),), send,
                           (ModelConfig("sentence-transformers/all-MiniLM-L6-v2"),))
    toolkit = tools_mod.Tools()
    hk = hooks_mod.Hooks()
    runner = ToolRunner(toolkit, hk)
    toolkit.gateway, toolkit.runner, toolkit.billing = gateway, runner, development_billing()
    toolkit.developer = FakeDeveloper(confirm)
    return toolkit, runner, hk, gateway, script


def call(runner, tool, **args):
    msg = runner.run(ToolCall("t", tool, args))
    return msg.error, (msg.content if msg.error else json.loads(msg.content))


def test_reads():
    tk, runner, *_ = make()
    err, acct = call(runner, "lookup_account", org="alderwick-labs")
    assert not err and acct["account"]["account_id"] == "practice-alderwick", acct
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")
    assert inv["console_confirmed_duplicates"][0]["duplicated_lines"] == ["PRACTICE-DUP-180/L2"], inv
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-LOOKALIKE")
    assert inv["console_confirmed_duplicates"] == [] and inv["same_amount_but_different_requests"], inv
    err, usage = call(runner, "get_usage", request_ids=["req_practice_market_01", "nope"])
    assert usage["usage"][0]["line_id"] is None and usage["not_found"] == ["nope"]
    err, res = call(runner, "search_knowledge", query="duplicate charge credit limit for Support")
    assert not err and res["results"], res
    print("reads ok")


def test_write_guards():
    tk, runner, hk, *_ = make()
    args = dict(account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180", line_id="PRACTICE-DUP-180/L2",
                amount_cents=18000, reason="duplicate", memo="dup of req_practice_dup_180")
    err, res = call(runner, "issue_credit_note", **args)
    assert err and "approval" in res, res  # no consent yet
    call(runner, "confirm_with_developer", proposal="Credit $180 on PRACTICE-DUP-180/L2")
    err, res = call(runner, "issue_credit_note", **{**args, "amount_cents": 99999})
    assert err and "exceeds" in res, res  # tool-level bound
    err, res = call(runner, "issue_credit_note", **args)
    assert err and "failed" in res, res  # blind retry after a failed write is held
    call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")  # read clears it
    err, res = call(runner, "issue_credit_note", **args)
    assert not err and res["issued"], res
    err, res = call(runner, "issue_credit_note", **args)
    assert err, res  # consent used / line already credited
    # card data
    assert "card number removed" in runner.user_message("my card is 4242 4242 4242 4242 exp 12/28")
    assert runner.user_message("invoice INV-2026-0042 total 1234") == "invoice INV-2026-0042 total 1234"
    # consent resets on a new developer message
    assert hk.approval is None
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="billing_ops", summary="x")
    assert err and "approval" in res
    call(runner, "confirm_with_developer", proposal="Hand to billing ops")
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="billing_ops",
                    summary="card 4242424242424242")
    assert err and "card" in res.lower(), res
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="nope", summary="x")
    assert err and "team must be one of" in res, res
    call(runner, "lookup_account", account_id="practice-alderwick")
    err, res = call(runner, "escalate_case", account_id="practice-alderwick", team="billing_ops",
                    summary="Test case", details={"invoice_id": "PRACTICE-DUP-180"})
    assert not err and res["escalated"], res
    err, res = call(runner, "open_dispute", account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
                    line_ids=["PRACTICE-DUP-180/L2"], reason="duplicate_charge", request_ids=["req_practice_dup_180"])
    assert err and "handed off" in res, res
    print("write guards ok")


def test_declined_consent():
    tk, runner, hk, *_ = make(confirm="no")
    call(runner, "confirm_with_developer", proposal="Credit $180")
    err, res = call(runner, "issue_credit_note", account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
                    line_id="PRACTICE-DUP-180/L2", amount_cents=18000, reason="duplicate")
    assert err and "approval" in res
    print("declined consent ok")


def test_dispute_then_escalate():
    tk, runner, hk, *_ = make()
    call(runner, "confirm_with_developer", proposal="Open a dispute on L2 and hand to Billing Ops")
    err, d = call(runner, "open_dispute", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                  line_ids=["PRACTICE-DUP-900/L2"], reason="duplicate_charge", request_ids=["req_practice_dup_900"])
    assert not err and d["dispute"]["status"] == "submitted", d
    err, c = call(runner, "escalate_case", account_id="practice-copperfern", team="billing_ops",
                  summary="Duplicate over limit", dispute_id=d["dispute"]["dispute_id"])
    assert not err, c
    print("dispute+escalate ok")


def test_skill_and_subagent():
    search = AssistantMessage(tool_calls=[ToolCall("s1", "search_knowledge", {"query": "who approves goodwill credits"})])
    final = AssistantMessage(content="ANSWER: Finance (FP&A) ...")
    tk, runner, hk, gw, script = make([search, final])
    err, res = call(runner, "load_skill", name="charge-review")
    assert not err and "console_confirmed_duplicates" in res["steps"]
    err, res = call(runner, "delegate", subagent="policy-researcher", task="Who approves goodwill credits?")
    assert not err and res["answer"].startswith("ANSWER"), res
    print("skill + subagent ok")


def test_agent_turns():
    """Drive agent.py through a user turn, a multi-tool round, and a text reply."""
    tk, runner, hk, gw, script = make()
    script += [
        AssistantMessage(tool_calls=[ToolCall("a", "lookup_account", {"org": "alderwick-labs"}),
                                     ToolCall("b", "get_invoice", {"invoice_id": "PRACTICE-DUP-180"})]),
        AssistantMessage(content="I found two lines billing the same request."),
    ]
    ctx = AgentBuildContext(ActionInterface(tuple(tk.get_tools())), KitResources.from_root(WS), gw,
                            {"company": "Redwood Inference", "role": "billing_support"}, tk.billing)
    import agent as agent_mod
    with activate_agent_context(ctx):
        ag = agent_mod.create_agent()
    state = ag.get_init_state([AssistantMessage(content="Hi! How can I help you today?")])
    reply, state = ag.generate_next_message(UserMessage(content="Our August invoice has $180 twice. Org alderwick-labs."), state)
    assert reply.is_tool_call()
    results = MultiToolMessage([runner.run(c) for c in reply.tool_calls])
    reply, state = ag.generate_next_message(results, state)
    assert reply.content.startswith("I found"), reply
    sys_notes = [m for m in state["messages"] if getattr(m, "role", "") == "system"]
    assert sys_notes and "auto-retrieved" in sys_notes[0].content
    print("agent turns ok; system prompt chars:", len(ag.instructions))


def test_chat_question_nudge():
    tk, runner, hk, gw, script = make()
    script += [AssistantMessage(content="Could you share your invoice ID?"),
               AssistantMessage(tool_calls=[ToolCall("q", "ask_developer", {"fields": ["invoice_id"]})])]
    ctx = AgentBuildContext(ActionInterface(tuple(tk.get_tools())), KitResources.from_root(WS), gw,
                            {"company": "Redwood Inference", "role": "billing_support"}, tk.billing)
    import agent as agent_mod
    with activate_agent_context(ctx):
        ag = agent_mod.create_agent()
    state = ag.get_init_state([AssistantMessage(content="Hi! How can I help you today?")])
    reply, state = ag.generate_next_message(UserMessage(content="I was double charged. Org alderwick-labs."), state)
    assert reply.is_tool_call() and reply.tool_calls[0].name == "ask_developer", reply
    print("chat-question nudge ok")


if __name__ == "__main__":
    test_chat_question_nudge()
    test_reads()
    test_write_guards()
    test_declined_consent()
    test_dispute_then_escalate()
    test_skill_and_subagent()
    test_agent_turns()
    print("ALL OK")
