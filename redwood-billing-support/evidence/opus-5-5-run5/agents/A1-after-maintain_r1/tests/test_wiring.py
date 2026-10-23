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
    assert not err and r["accounts"][0]["account"]["account_id"] == "practice-alderwick", r
    err, inv = call(runner, "get_invoice", invoice_id="PRACTICE-DUP-180")
    da = inv["duplicate_analysis"][0]
    assert da["original_line"] == "PRACTICE-DUP-180/L1" and da["copy_lines"] == ["PRACTICE-DUP-180/L2"], da

    args = dict(account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180", line_id="PRACTICE-DUP-180/L2",
                amount="180.00", memo="dup")
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "confirm_with_developer" in r, r                     # no consent yet
    call(runner, "confirm_with_developer", proposal="Credit $90.00 somewhere")
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "does not name" in r, r                              # consent for something else
    call(runner, "confirm_with_developer", proposal="Credit note for the full $180.00 on line PRACTICE-DUP-180/L1")
    err, r = call(runner, "issue_duplicate_credit", **{**args, "line_id": "PRACTICE-DUP-180/L1"})
    assert err and "is the original" in r, r                            # never the original
    call(runner, "confirm_with_developer", proposal="I'll issue a credit note of $180.00 on the duplicated line L2 of invoice PRACTICE-DUP-180")
    err, r = call(runner, "issue_duplicate_credit", **{**args, "amount": "90.00"})
    assert err and ("full amount" in r or "does not state" in r), r     # partial credit refused
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert not err and r["ok"] and r["credit_note"]["amount"] == "$180.00", r
    err, r = call(runner, "issue_duplicate_credit", **args)
    assert err and "already exists" in r, r                             # never twice
    err, act = call(runner, "get_account_activity", account_id="practice-alderwick")
    assert len(act["credit_notes"]) == 1, act

    from knowledge import duplicate_credit_limit_cents
    for start, want in (("2026-08-01", 50000), ("2027-03-01", 25000), ("2027-04-01", 25000), (None, 25000)):
        limit, src = duplicate_credit_limit_cents(start)
        print("limit", start, limit, src)
        assert limit == want, (start, limit, src)

    # enterprise / contract accounts: no credit of any amount -> dispute + Billing Ops (priority normal)
    for org, inv_id, amt in (("practice-copperfern", "PRACTICE-DUP-900", "900.00"), ("practice-ironbark", "PRACTICE-R1-DUP-60", "60.00")):
        t, h, runner, _ = make()
        call(runner, "confirm_with_developer", proposal=f"credit {inv_id}/L2 ${amt}")
        err, r = call(runner, "issue_duplicate_credit", account_id=org, invoice_id=inv_id, line_id=f"{inv_id}/L2", amount=amt)
        assert err and "self-serve" in r, r
    call(runner, "confirm_with_developer", proposal="Open a dispute on the copy line PRACTICE-R1-DUP-60/L2 and pass it to Billing Ops")
    err, d = call(runner, "open_dispute", account_id="practice-ironbark", invoice_id="PRACTICE-R1-DUP-60",
                  line_ids=["PRACTICE-R1-DUP-60/L2"], reason="duplicate_charge", request_ids=["req_practice_r1_dup_60"],
                  window="2027-03-18 10:00 UTC")
    assert not err and d["dispute"]["status"] == "submitted", d
    err, c = call(runner, "escalate_case", account_id="practice-ironbark", team="billing_ops",
                  summary="Confirmed duplicate on a contract account; dispute opened.",
                  details={"invoice_id": "PRACTICE-R1-DUP-60", "note": "card 4242 4242 4242 4242"},
                  dispute_id=d["dispute"]["dispute_id"])
    assert not err and c["ok"] and c["case"]["priority"] == "normal", c
    err, r = call(runner, "lookup_account", org="ironbark-robotics")
    assert err and "already handed" in r, r

    # look-alike refused; unconfirmed handoff needs no confirmation
    t, h, runner, _ = make()
    call(runner, "confirm_with_developer", proposal="credit PRACTICE-LOOKALIKE/L2 $180.00")
    err, r = call(runner, "issue_duplicate_credit", account_id="practice-dawnmere", invoice_id="PRACTICE-LOOKALIKE",
                  line_id="PRACTICE-LOOKALIKE/L2", amount="180.00")
    assert err and "does not confirm" in r, r
    t, h, runner, _ = make()
    err, c = call(runner, "escalate_case", account_id="practice-dawnmere", team="billing_ops", summary="unconfirmed duplicate")
    assert not err and c["ok"], c

    # marketplace and usage times
    err, r = call(make()[2], "lookup_account", org="kestrel-vision")
    assert r["accounts"][0]["account"]["billing_channel"] == "gcp_marketplace"
    err, u = call(make()[2], "get_usage", request_ids=["req_practice_r1_market_01", "nope"])
    assert u["rows"][0]["line_id"] is None and u["not_found"] == ["nope"] and u["rows"][0]["at_billing_timezone"], u

    # credit balance from the ledger, not the frozen grants table
    err, b = call(make()[2], "get_credit_balance", account_id="practice-juniper")
    assert b["source"].startswith("credit_ledger") and b["grants"][0]["balance"] == "$120.00", b
    assert b["grants"][0]["expires_on"] == "2027-04-10", b

    # declined consent blocks every write, including a case
    t, h, runner, _ = make(confirm="no")
    call(runner, "confirm_with_developer", proposal="credit PRACTICE-DUP-180/L2 $180.00")
    err, r = call(runner, "escalate_case", account_id="practice-alderwick", team="billing_ops", summary="x")
    assert err and "declined" in r, r

    # card masking on developer messages: nothing of the number survives
    m = runner.user_message("my new card is 4111 1111 1111 1111 exp 12/29")
    assert "1111" not in m and "card number removed" in m, m
    m = runner.user_message("it's four one one one one one one one one one one one one one one one")
    assert "four one one" not in m, m
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
