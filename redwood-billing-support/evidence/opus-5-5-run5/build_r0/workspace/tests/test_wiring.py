"""Deterministic wiring checks (no support-model calls). Run from the workspace:

    python tests/test_wiring.py

Uses redwood_kit.billing.development_billing() (practice console; writes are local) and a scripted
model gateway. Embeddings run locally.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redwood_kit.agent_context import ModelConfig, ModelGateway  # noqa: E402
from redwood_kit.billing import development_billing  # noqa: E402
from redwood_kit.embeddings import embed  # noqa: E402
from redwood_kit.hooks import ToolRunner  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, UserMessage, to_dict  # noqa: E402

import hooks as hooks_mod  # noqa: E402
import tools as tools_mod  # noqa: E402


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
    def send(payload):
        if payload["call"] == "embed":
            return {"vectors": embed(model=payload["model"], inputs=payload["inputs"])}
        assert payload["call"] == "model"
        return {"message": to_dict(script(payload))}
    gw = ModelGateway((ModelConfig("claude-sonnet-5-5"),), send,
                      (ModelConfig("sentence-transformers/all-MiniLM-L6-v2"),))
    t = tools_mod.Tools()
    h = hooks_mod.Hooks()
    runner = ToolRunner(t, h)
    t.gateway, t.runner, t.billing, t.developer = gw, runner, development_billing(), FakeDeveloper(confirm)
    return t, h, runner, gw


def call(runner, tool_name, **args):
    msg = runner.run(ToolCall(f"c-{tool_name}", tool_name, args))
    return msg.error, (msg.content if msg.error else json.loads(msg.content))


def main():
    t, h, runner, _ = make()
    names = {x.name for x in t.get_tools()}
    assert {"search_knowledge", "lookup_account", "get_invoice", "issue_duplicate_credit", "open_dispute",
            "escalate_case", "ask_developer", "confirm_with_developer", "load_skill", "delegate"} <= names, names

    err, r = call(runner, "lookup_account", org="alderwick-labs")
    assert not err and r["accounts"][0]["account"]["account_id"] == "practice-alderwick"
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")
    assert inv["lines_billing_identical_request_ids"] == [["PRACTICE-DUP-180/L1", "PRACTICE-DUP-180/L2"]]

    args = dict(account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180", line_id="PRACTICE-DUP-180/L2",
                amount_cents=18000, memo="dup")
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "approval" in r, r                                   # no consent yet
    call(runner, "confirm_with_developer", proposal="Credit $90.00 somewhere")
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "does not name" in r, r                              # consent for something else
    call(runner, "confirm_with_developer", proposal="Issue a $180.00 credit note on line PRACTICE-DUP-180/L2")
    err, r = call(runner, "issue_duplicate_credit", **{**args, "amount_cents": 9000})
    assert err and "full amount" in r, r                                # partial credit refused
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert not err and r["ok"], r                                       # the real credit
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "already exists" in r, r                             # never twice

    # over the limit: the hook reads the limit from the indexed approval matrix
    from knowledge import duplicate_credit_limit_cents
    limit, src = duplicate_credit_limit_cents()
    print("limit from index:", limit, src)
    assert limit == 50000
    t, h, runner, _ = make()
    call(runner, "confirm_with_developer", proposal="credit PRACTICE-DUP-900/L2 $900.00")
    err, r = call(runner, "issue_duplicate_credit", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                  line_id="PRACTICE-DUP-900/L2", amount_cents=90000)
    assert err and "over Support's duplicate-credit limit" in r, r
    err, d = call(runner, "open_dispute", account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
                  line_ids=["PRACTICE-DUP-900/L2"], reason="duplicate_charge", request_ids=["req_practice_dup_900"])
    assert not err and d["dispute"]["status"] == "submitted", d
    err, c = call(runner, "escalate_case", account_id="practice-copperfern", team="billing_ops",
                  summary="Duplicate $900 dedicated line over Support limit; dispute opened.",
                  details={"invoice_id": "PRACTICE-DUP-900", "card": "4242 4242 4242 4242"},
                  dispute_id=d["dispute"]["dispute_id"])
    assert not err and c["ok"], c
    err, r = call(runner, "lookup_account", org="copperfern-data")
    assert err and "already handed" in r, r

    # look-alike and marketplace are refused by the hook
    t, h, runner, _ = make()
    call(runner, "confirm_with_developer", proposal="credit PRACTICE-LOOKALIKE/L2 $180.00")
    err, r = call(runner, "issue_duplicate_credit", account_id="practice-dawnmere", invoice_id="PRACTICE-LOOKALIKE",
                  line_id="PRACTICE-LOOKALIKE/L2", amount_cents=18000)
    assert err and "does not confirm a duplicate" in r, r
    err, r = call(runner, "lookup_account", org="emberquarry-systems")
    assert r["accounts"][0]["account"]["billing_channel"] == "aws_marketplace"
    err, u = call(runner, "get_usage", request_ids=["req_practice_market_01", "nope"])
    assert u["rows"][0]["line_id"] is None and u["not_found"] == ["nope"]

    # declined consent blocks writes
    t, h, runner, _ = make(confirm="no")
    call(runner, "confirm_with_developer", proposal="hand to finance")
    err, r = call(runner, "escalate_case", account_id="practice-brackenvale", team="account_team", summary="x")
    assert err and "did not approve" in r, r

    # card masking on developer messages
    assert "ending 1111" in runner.user_message("my new card is 4111 1111 1111 1111 exp 12/29")
    assert runner.user_message("invoice INV-2026-000123 total 1234") == "invoice INV-2026-000123 total 1234"

    # skill loading
    err, s = call(runner, "load_skill", name="duplicate-charge-resolution")
    assert not err and "confirm_with_developer" in s["steps"]

    # delegate: the subagent runs search_knowledge through the same runner, then answers
    seen = []

    def script(payload):
        msgs = payload["messages"]
        seen.append([tl["name"] for tl in payload.get("tools") or []])
        if msgs[0]["content"].startswith("You research"):
            if msgs[-1]["role"] == "user":
                return AssistantMessage(tool_calls=[ToolCall("s1", "search_knowledge",
                                                             {"query": "duplicate credit limit proposal 1500"})])
            res = msgs[-1]["tool_messages"][0]
            assert not res["error"] and "documents" in json.loads(res["content"])
            return AssistantMessage(content="ANSWER: limit per approval matrix; the $1,500 proposal is a parked draft.")
        return AssistantMessage(content="done")
    t, h, runner, gw = make(script)
    err, r = call(runner, "delegate", subagent="policy-researcher", task="Is the $1,500 duplicate limit in effect?")
    assert not err and "parked draft" in r["answer"], r
    assert seen[0] == ["search_knowledge"], seen

    # agent loop: a chat question is sent back once; the retry's tool call is kept
    import agent as agent_mod
    replies = iter([AssistantMessage(content="Could you share your org id?"),
                    AssistantMessage(tool_calls=[ToolCall("a1", "ask_developer", {"fields": ["org_id"]})])])

    class Ctx:
        model_gateway = ModelGateway((ModelConfig("claude-sonnet-5-5"),), lambda p: {"message": to_dict(next(replies))})
        class action_interface:  # noqa: N801
            available = tuple(t.get_tools())
    ag = agent_mod.Agent(Ctx)
    st = ag.get_init_state([AssistantMessage(content="Hi! How can I help you today?")])
    reply, st = ag.generate_next_message(UserMessage(content="I was double charged"), st)
    assert reply.is_tool_call() and reply.tool_calls[0].name == "ask_developer"
    assert len(st["messages"]) == 3
    print("wiring checks passed")


if __name__ == "__main__":
    main()
