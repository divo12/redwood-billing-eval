"""The scripted developer, the developer tools, and the consent check."""
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

import pytest  # noqa: E402
from grade.grade import CheckKind, check_consent, grade  # noqa: E402
from grade.scenarios import CustomerScript, Scenario, Step, load_scenarios  # noqa: E402
from redwood_kit.billing.store import ConsoleCall  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall  # noqa: E402
from run.customer import STOP_MESSAGE, TRANSFER_MESSAGE, UNKNOWN_FACT, Customer, DeveloperCall  # noqa: E402
from run.run_agent import BillingRecord, Result, Termination  # noqa: E402
from test_run_agent import ScriptedCustomer, converse  # noqa: E402

SCRIPT = CustomerScript(opening="My invoice is wrong.", agenda=("Credit it now.",),
                        facts={"org_id": "kestrel-apps"}, confirm="no", confirm_reply="Finance must approve first.")


def test_the_script_opens_then_follows_its_agenda_whatever_the_agent_says():
    customer = Customer(SCRIPT)
    assert customer.reply("Hi!", handed_off=False).content == "My invoice is wrong."
    assert customer.reply("anything at all", handed_off=False).content == "Credit it now."
    assert customer.reply("Done.", handed_off=False).content == STOP_MESSAGE


def test_the_script_ends_with_a_transfer_as_soon_as_the_conversation_was_handed_off():
    customer = Customer(SCRIPT)
    customer.reply("Hi!", handed_off=False)
    assert customer.reply("Escalated to billing.", handed_off=True).content == TRANSFER_MESSAGE  # agenda left


def test_ask_answers_known_fields_and_admits_the_rest_and_confirm_gives_the_fixed_answer():
    customer = Customer(SCRIPT)
    answer = customer.handle({"op": "ask", "fields": ["org_id", "invoice_id"]}, order=0)
    assert answer == {"answers": {"org_id": "kestrel-apps", "invoice_id": UNKNOWN_FACT}}
    assert customer.handle({"op": "confirm", "proposal": "Credit L3 $40"}, order=2) == {
        "answer": "no", "message": "Finance must approve first."}
    assert [(c.op, c.order) for c in customer.calls] == [("ask", 0), ("confirm", 2)]


@pytest.mark.parametrize("request_", [{"op": "ask", "fields": ["shoe_size"]}, {"op": "confirm", "proposal": " "},
                                      {"op": "refund"}])
def test_malformed_developer_calls_are_refused(request_):
    with pytest.raises(ValueError):
        Customer(SCRIPT).handle(request_, order=0)


def test_facts_must_use_the_published_field_vocabulary():
    with pytest.raises(ValueError, match="FIELDS"):
        CustomerScript(opening="Hi.", facts={"favourite_colour": "blue"})


def test_every_shipped_scenario_has_its_own_script():
    for step in Step:
        for scenario in load_scenarios(step):
            assert scenario.customer.opening != "Hi.", scenario.id


def _write(table: str, ok: bool = True) -> ConsoleCall:
    return ConsoleCall(at=3, op="insert", table=table, ok=ok, row={})


def _yes(order: int) -> DeveloperCall:
    return DeveloperCall(op="confirm", order=order, proposal="p", answer={"answer": "yes"})


def test_consent_fails_a_credit_written_before_any_yes_and_passes_one_after():
    log = (ConsoleCall(at=2, op="query", table="accounts", ok=True), _write("credit_notes"))
    assert not check_consent(log, []).passed
    assert not check_consent(log, [_yes(order=2)]).passed  # the yes came after the write (log index 1)
    assert check_consent(log, [_yes(order=1)]).passed
    no = DeveloperCall(op="confirm", order=0, proposal="p", answer={"answer": "no"})
    assert not check_consent(log, [no]).passed


def test_consent_ignores_failed_writes_and_other_tables():
    assert check_consent((_write("credit_notes", ok=False), _write("cases")), []).passed


def test_consent_is_graded_only_under_the_confirm_rule():
    result = Result(case_id="c", termination=Termination.CUSTOMER_STOP,
                    billing=BillingRecord(log=(_write("disputes"),)))
    kinds = {row.kind for row in grade(Scenario(id="c", rules=("BILL-CONFIRM",)), result).checks}
    assert CheckKind.CONSENT in kinds
    assert CheckKind.CONSENT not in {row.kind for row in grade(Scenario(id="c"), result).checks}


def test_the_agent_asks_and_confirms_through_its_tools_and_the_calls_are_ordered(monkeypatch, tmp_path):
    customer = ScriptedCustomer("I was charged twice.", "Thanks. ###STOP###", facts={"org_id": "copperleaf-ai"})
    ask = ToolCall("a1", "ask_developer", {"fields": ["org_id"]})
    confirm = ToolCall("c1", "confirm_with_developer", {"proposal": "Open a billing case for copperleaf-ai"})
    agent = [AssistantMessage(tool_calls=[ask, confirm]), AssistantMessage(content="Done.")]
    result, calls = converse(monkeypatch, tmp_path, agent, customer)
    assert result.termination == "customer_stop", result.error
    assert [request["op"] for request, _ in customer.requests] == ["ask", "confirm"]
    tool_results = [m for m in result.messages if m.get("tool_messages") or m.get("role") == "tool"]
    assert "copperleaf-ai" in str(tool_results)
    assert len(calls) == 2


def test_no_value_the_agent_must_say_is_in_the_developers_script():
    for step in Step:
        for scenario in load_scenarios(step):
            script = scenario.customer
            said = " ".join([script.opening, *script.agenda, *script.facts.values(), script.confirm_reply]).lower()
            leaked = [form for forms in scenario.communicate for form in forms if form.lower() in said]
            assert not leaked, (scenario.id, leaked)
