import sys, json
sys.path.insert(0, '/app/workspace')
from redwood_kit.billing import development_billing
from redwood_kit.hooks import ToolRunner
from redwood_kit.messages import ToolCall
import tools, hooks
from session import SESSION
from redwood_kit.embeddings import embed

class FakeGW:
    def embed(self, model, inputs): return embed(model=model, inputs=inputs)
class FakeDev:
    def __init__(s, ans='yes'): s.ans=ans
    def ask(s, fields): return {"answers": {f: "I don't have that." for f in fields}}
    def confirm(s, proposal): return {"answer": s.ans, "message": "ok"}

def run(name, **args):
    m = runner.run(ToolCall(id='x', name=name, arguments=args))
    print(f"--> {name} {json.dumps(args)[:120]}\n   error={m.error} {m.content[:600]}")
    return m

for acct, inv, line, amt in [('practice-alderwick','PRACTICE-DUP-180','PRACTICE-DUP-180/L2',18000),
                             ('practice-copperfern','PRACTICE-DUP-900','PRACTICE-DUP-900/L2',90000),
                             ('practice-dawnmere','PRACTICE-LOOKALIKE','PRACTICE-LOOKALIKE/L2',18000)]:
    SESSION.confirmations.clear(); SESSION.writes.clear(); SESSION.handed_off=False
    b = development_billing(); SESSION.billing = b
    t = tools.Tools(); t.billing=b; t.gateway=FakeGW(); t.developer=FakeDev()
    runner = ToolRunner(t, hooks.Hooks()); t.runner=runner
    print('=====', acct)
    run('issue_credit_note', account_id=acct, invoice_id=inv, line_id=line, amount_cents=amt, reason='duplicate')
    run('confirm_with_developer', proposal=f'credit {line}')
    run('issue_credit_note', account_id=acct, invoice_id=inv, line_id=line, amount_cents=amt-100, reason='duplicate')
    run('issue_credit_note', account_id=acct, invoice_id=inv, line_id=line, amount_cents=amt, reason='duplicate')
    run('issue_credit_note', account_id=acct, invoice_id=inv, line_id=line.replace('L2','L1'), amount_cents=amt, reason='duplicate')
print(hooks.Hooks().a_redact_card_numbers("my new card is 4242 4242 4242 4242 exp 12/29 cvv 123, request req_123456789012345"))
run('escalate_case', account_id='practice-dawnmere', team='Billing Ops', summary='x 4242424242424242')
run('escalate_case', account_id='practice-dawnmere', team='Billing Ops', summary='lookalike lines')
run('escalate_case', account_id='practice-dawnmere', team='finance', summary='again')
run('search_knowledge', query='who handles SLA credit request for contract customer', scope='policy')
print(t.load_skill('billing-case-procedure')['name'])
run('get_invoice', invoice_id='PRACTICE-DUP-180')
run('lookup_account', org='emberquarry-systems')
