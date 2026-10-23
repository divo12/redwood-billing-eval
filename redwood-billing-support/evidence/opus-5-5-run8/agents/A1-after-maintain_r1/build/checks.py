"""Build-time checks of tools and hooks against the release's practice console (no model calls).

Run from anywhere: python /app/workspace/build/checks.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from redwood_kit.billing import development_billing  # noqa: E402
from redwood_kit.embeddings import embed  # noqa: E402
from redwood_kit.hooks import ToolRunner  # noqa: E402
from redwood_kit.messages import ToolCall  # noqa: E402

import hooks  # noqa: E402
import tools  # noqa: E402
from session import SESSION  # noqa: E402


class FakeGW:
    def embed(self, model, inputs):
        return embed(model=model, inputs=inputs)


class FakeDev:
    def __init__(self, answer="yes"):
        self.answer = answer

    def ask(self, fields):
        return {"answers": {f: "I don't have that." for f in fields}}

    def confirm(self, proposal):
        return {"answer": self.answer, "message": "ok"}


FAILS = []


def fresh(answer="yes"):
    SESSION.confirmations.clear(); SESSION.writes.clear(); SESSION.handed_off = False
    b = development_billing(); SESSION.billing = b
    t = tools.Tools(); t.billing = b; t.gateway = FakeGW(); t.developer = FakeDev(answer)
    r = ToolRunner(t, hooks.Hooks()); t.runner = r
    return t, r


def run(r, name, expect_error, **args):
    m = r.run(ToolCall(id="x", name=name, arguments=args))
    ok = m.error == expect_error
    print(f"{'ok  ' if ok else 'FAIL'} {name} {json.dumps(args)[:110]}\n     -> {m.content[:260]}")
    if not ok:
        FAILS.append((name, args))
    return m


# self-serve, 2026 invoice, $180 copy line within the earlier-price-book limit
t, r = fresh()
run(r, "issue_credit_note", True, account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
    line_id="PRACTICE-DUP-180/L2", amount=180.0, reason="duplicate")            # no consent yet
run(r, "confirm_with_developer", False, proposal="credit L2 $180")
run(r, "issue_credit_note", True, account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
    line_id="PRACTICE-DUP-180/L1", amount=180.0, reason="duplicate")            # original line
run(r, "issue_credit_note", True, account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
    line_id="PRACTICE-DUP-180/L2", amount=90.0, reason="duplicate")             # partial
run(r, "issue_credit_note", False, account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
    line_id="PRACTICE-DUP-180/L2", amount=180.0, reason="duplicate")
run(r, "issue_credit_note", True, account_id="practice-alderwick", invoice_id="PRACTICE-DUP-180",
    line_id="PRACTICE-DUP-180/L2", amount=180.0, reason="duplicate")            # twice

# contract account: no credit notes at all; dispute the copy and hand off
t, r = fresh()
run(r, "confirm_with_developer", False, proposal="dispute")
run(r, "issue_credit_note", True, account_id="practice-ironbark", invoice_id="PRACTICE-R1-DUP-60",
    line_id="PRACTICE-R1-DUP-60/L2", amount=60.0, reason="duplicate")
run(r, "open_dispute", True, account_id="practice-ironbark", invoice_id="PRACTICE-R1-DUP-60",
    line_ids=["PRACTICE-R1-DUP-60/L1"], reason="duplicate_charge", request_ids=["req_practice_r1_dup_60"])
m = run(r, "open_dispute", False, account_id="practice-ironbark", invoice_id="PRACTICE-R1-DUP-60",
        line_ids=["PRACTICE-R1-DUP-60/L2"], reason="duplicate_charge", request_ids=["req_practice_r1_dup_60"])
dsp = json.loads(m.content)["dispute"]["dispute_id"]
run(r, "escalate_case", False, account_id="practice-ironbark", team="Billing Ops", summary="dup on contract acct")  # dispute_id auto-linked
run(r, "escalate_case", True, account_id="practice-ironbark", team="finance", summary="again")

# over the limit
t, r = fresh()
run(r, "confirm_with_developer", False, proposal="credit")
run(r, "issue_credit_note", True, account_id="practice-copperfern", invoice_id="PRACTICE-DUP-900",
    line_id="PRACTICE-DUP-900/L2", amount=900.0, reason="duplicate")

# declined -> nothing written, not even a case
t, r = fresh("no")
run(r, "confirm_with_developer", False, proposal="credit")
run(r, "escalate_case", True, account_id="practice-alderwick", team="billing_ops", summary="x")

# reads
t, r = fresh()
run(r, "get_credit_balance", False, account_id="practice-juniper")
run(r, "get_invoice", False, invoice_id="PRACTICE-R1-DUP-60")
run(r, "get_invoice", False, invoice_id="PRACTICE-LOOKALIKE")
run(r, "lookup_account", False, org="kestrel-vision")
run(r, "escalate_case", True, account_id="practice-dawnmere", team="billing_ops", summary="card 4242 4242 4242 4242")
print(hooks.Hooks().a_redact_card_numbers("new card 4111 1111 1111 1111 exp 10/28 cvv 123, or four one one one "
                                          "one one one one one one one one one one one one"))
print("FAILS:", FAILS)
