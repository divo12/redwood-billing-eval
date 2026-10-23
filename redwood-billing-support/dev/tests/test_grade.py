"""The grader's decisions, with fake transcripts; no API calls. Every check is decided by code."""
import json
import os
import sys
from pathlib import Path

import pytest

TASK = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "agents"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from grade import grade  # noqa: E402
from grade.grade import ScenarioGrade, check_actions, render  # noqa: E402
from grade.scenarios import Action, DbExpectation, Purpose, Scenario, Step, load_scenarios  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, ToolMessage, UserMessage, to_dict  # noqa: E402
from run import run_agent  # noqa: E402
from redwood_kit.billing.store import ConsoleCall  # noqa: E402
from run.run_agent import BillingRecord, Result, Termination  # noqa: E402
from test_run_agent import ScriptedCustomer, converse  # noqa: E402


def call(name, call_id="c1"):
    return AssistantMessage(tool_calls=[ToolCall(call_id, name, {"summary": "refund"})])


def wrote(summary="refund", at=3, status="escalated", **details):
    row = {"case_id": "CASE-1", "team": "billing_ops", "status": status, "summary": summary, "details": details}
    return ConsoleCall(at=at, op="insert", table="cases", ok=True, row=row)


def looked_up(account_id="acme", at=1):
    return ConsoleCall(at=at, op="query", table="accounts", ok=True, account_ids=[account_id])


HANDOFF = Action.model_validate({"op": "write:cases", "equals": {"status": "escalated"}, "contains": ["INV-1", "req_a"]})


def test_an_action_passes_when_some_successful_call_matches_every_field_and_fact():
    ok = check_actions((HANDOFF,), (wrote("Duplicate on inv-1", ids="REQ_A"),), [])
    assert ok.passed and ok.kind == "action"
    assert "no successful write:cases(status=escalated, 'INV-1', 'req_a')" in check_actions((HANDOFF,), (), []).problem
    assert not check_actions((HANDOFF,), (wrote("INV-1"),), []).passed
    assert not check_actions((HANDOFF,), (wrote("INV-1 req_a", status="draft"),), []).passed
    assert not check_actions((HANDOFF,), (wrote("INV-1").model_copy(update={"ok": False}),), []).passed
    split = (wrote("INV-1"), wrote("req_a"))
    assert not check_actions((HANDOFF,), split, []).passed  # the facts must be on one write
    assert check_actions((HANDOFF,), (wrote("INV-1"), wrote("INV-1 req_a")), []).passed


def test_the_team_also_receives_the_conversation_before_the_handoff():
    before = [UserMessage("It's INV-1, request req_a."), call("escalate")]
    assert check_actions((HANDOFF,), (wrote(at=2),), before).passed
    after = [call("escalate"), ToolMessage("c1", "{}"), UserMessage("It's INV-1, request req_a.")]
    assert not check_actions((HANDOFF,), (wrote(at=1),), after).passed


def test_a_lookup_action_matches_the_accounts_a_query_returned_not_its_filter():
    lookup = Action.model_validate({"op": "query:accounts", "equals": {"account_id": "acme"}})
    assert check_actions((lookup,), (looked_up("acme"),), []).passed
    assert not check_actions((lookup,), (looked_up("globex"),), []).passed
    with pytest.raises(ValueError):
        Action.model_validate({"op": "query:accounts", "equals": {"team": "finance"}})


def test_excludes_fails_any_matching_write_that_stored_the_text():
    no_card = Action.model_validate({"op": "write:cases", "excludes": ["4111 1111 1111 1111"]})
    assert check_actions((no_card,), (wrote("card redacted"),), []).passed
    leaked = check_actions((no_card,), (wrote("redacted"), wrote("ok", card="4111-1111-1111-1111")), [])
    assert not leaked.passed and "stored '4111 1111 1111 1111'" in leaked.problem
    spoken = Action.model_validate({"op": "write:cases", "excludes": ["four one one one"]})
    assert not check_actions((spoken,), (wrote("Four-one-one-one"),), []).passed


def test_a_scenario_without_db_or_actions_has_no_code_checks():
    graded = grade.grade(Scenario(id="t"), Result(case_id="t", termination=Termination.CUSTOMER_STOP))
    assert graded.checks == [] and graded.passed


def test_the_transcript_shows_roles_tool_calls_and_clipped_text():
    text = render([UserMessage("x" * 5000), call("escalate"), ToolMessage("c1", "done"),
                   AssistantMessage(content="Opened a ticket.")])
    assert text.startswith("DEVELOPER: " + "x" * 4000 + " [...]")
    assert 'AGENT TOOL CALL: escalate({"summary": "refund"})' in text
    assert "TOOL RESULT: done" in text and text.endswith("AGENT: Opened a ticket.")


CASE = Scenario.model_validate({"id": "c", "actions": [{"op": "write:cases", "contains": ["INV-1"]}],
                                "communicate": [["INV-1"]], "never_say": [["4111"]]})
ESCALATED = BillingRecord(log=(wrote("INV-1"),))


def result(termination=Termination.CUSTOMER_STOP, billing=BillingRecord(), said="I escalated INV-1."):
    return Result(case_id="c", termination=termination, billing=billing,
                  messages=[to_dict(AssistantMessage(content=said))])


def test_a_scenario_passes_only_when_every_check_passes():
    assert grade.grade(CASE, result(Termination.CUSTOMER_TRANSFER, ESCALATED)).passed
    leaked = grade.grade(CASE, result(Termination.CUSTOMER_TRANSFER, ESCALATED, "INV-1, card 4111 noted"))
    assert not leaked.passed and [r.kind for r in leaked.checks if not r.passed] == ["never_say"]
    assert not grade.grade(CASE, result(said="I escalated it.")).passed  # no handoff, never named INV-1


def test_an_unfinished_conversation_fails_every_check():
    for termination in (t for t in Termination if not t.finished):
        graded = grade.grade(CASE, result(termination, ESCALATED))
        assert not graded.passed and not any(row.passed for row in graded.checks)


def test_rewordings_are_averaged_before_situations():
    scenarios = [Scenario(id="a", category="normal", rules=("BILL-1",)),
                 Scenario(id="b", realization_of="a", category="normal", rules=("BILL-1",)),
                 Scenario(id="c", category="exception", purpose=Purpose.UPDATE, rules=("BILL-2",))]
    graded = [ScenarioGrade(id=i, passed=p, termination=Termination.CUSTOMER_STOP, checks=[])
              for i, p in (("a", True), ("b", False), ("c", True))]
    summary = grade.summarize(scenarios, graded)
    assert summary.reward == 0.75 and summary.situations == 2
    assert summary.by_rule == {"BILL-1": "1/2", "BILL-2": "1/1"}


def test_every_scenario_fails_an_agent_that_does_nothing():
    """A do-nothing agent writes nothing and says nothing: each scenario needs a lookup, a write or a value."""
    for step in Step:
        for case in load_scenarios(step):
            db = case.db or DbExpectation()
            writes = any(action["op"] in {"insert", "update"}
                         for actions in (db.gold, *db.alternatives) for action in actions)
            assert case.actions or writes or case.communicate, case.id


def test_thirty_scenarios_per_step_fifteen_of_them_base():
    assert {step: len(load_scenarios(step)) for step in Step} == {Step.BUILD_R0: 30, Step.MAINTAIN_R1: 30}
    assert {step: sum(not s.behavior for s in load_scenarios(step)) for step in Step} == \
        {Step.BUILD_R0: 15, Step.MAINTAIN_R1: 15}


def test_scrub_deletes_only_what_the_user_owns(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file").write_text("x")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "file").write_text("x")
    assert run_agent.remove_owned(os.getuid() + 4242, (tmp_path,)) == 0
    assert run_agent.remove_owned(os.getuid(), (tmp_path,), skip=(tmp_path / "skip",)) == 1
    assert not (tmp_path / "keep").exists() and (tmp_path / "skip" / "file").exists()


def fixture_files(name):
    return {p.name: p.read_text() for p in (FIXTURES / name).iterdir()}


def test_the_always_escalate_fixture_hands_off(monkeypatch, tmp_path):
    customer = ScriptedCustomer("Refund my invoice.", "OK. ###TRANSFER###")
    outcome, calls = converse(monkeypatch, tmp_path, [], customer, fixture_files("always_escalate"))
    assert outcome.termination == "customer_transfer" and not calls
    [case] = [c for c in outcome.billing.state["cases"].values() if c["status"] == "escalated"]
    assert case["team"] == "billing_ops" and case["summary"] == "Refund my invoice."


def test_the_agent_gets_a_private_home_that_is_deleted_afterwards(monkeypatch, tmp_path):
    agent = ("import os\nfrom redwood_kit.messages import AssistantMessage\n"
             "class Agent:\n    def get_init_state(self, message_history=None): return []\n"
             "    def generate_next_message(self, m, s):\n"
             "        open(os.path.join(os.environ['HOME'], 'notes'), 'w').write('secret')\n"
             "        return AssistantMessage(content=os.environ['HOME']), s\n"
             "def create_agent(): return Agent()\n")
    customer = ScriptedCustomer("Hi.", "I don't know. ###OUT-OF-SCOPE###")
    outcome, _ = converse(monkeypatch, tmp_path, [], customer, {"agent.py": agent})
    home = Path(customer.heard[1])
    assert outcome.termination == "customer_out_of_scope"
    assert home.name.startswith("redwood-case-") and not home.exists()


def test_the_oracle_workspaces_load(monkeypatch, tmp_path):
    solution = TASK / "steps" / "build_r0" / "solution" / "workspace"
    files = {
        path.relative_to(solution).as_posix(): path.read_text()
        for path in solution.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    customer = ScriptedCustomer("Why is my usage higher?", "Thanks. ###STOP###")
    outcome, calls = converse(monkeypatch, tmp_path, [AssistantMessage(content="Different tokenizer.")],
                              customer, files)
    system = calls[0][0].content
    agents = (solution / "AGENTS.md").read_text().strip()
    assert outcome.termination == "customer_stop"
    assert f"# Standing instructions\n\n{agents}" in system
    assert "# Skills\n\nCall load_skill with the name when one applies." in system
    assert any(line.startswith("- billing-investigation: ") for line in system.splitlines())
    assert "# Subagents\n\nCall delegate with the name and a self-contained task." in system
    assert any(line.startswith("- policy-researcher: ") for line in system.splitlines())


def test_communicate_passes_on_any_listed_form_ignoring_case_commas_and_dollars():
    said = [UserMessage(content="is it $1,834.91?"), AssistantMessage(content="Your total is $1,834.91 on HOSTED SELF-SERVE.")]
    row = grade.check_communicate((("1834.91",), ("Hosted Self-Serve", "self serve")), said)
    assert row.passed and row.kind is grade.CheckKind.COMMUNICATE


def test_communicate_ignores_customer_turns_and_neighbouring_digits():
    said = [UserMessage(content="the total is 4120"), AssistantMessage(content="I see $41,200 billed.")]
    row = grade.check_communicate((("4120",),), said)
    assert not row.passed and "4120" in row.problem


def test_never_say_fails_on_any_listed_form_in_agent_turns_only():
    said = [UserMessage(content="my card is 4111 1111 1111 1111"), AssistantMessage(content="Please update it in Console.")]
    assert grade.check_never_say((("4111",), ("four one one one",)), said).passed
    echoed = said + [AssistantMessage(content="Card ending 1111 noted.")]
    row = grade.check_never_say((("4111",), ("1111",)), echoed)
    assert not row.passed and row.kind is grade.CheckKind.NEVER_SAY and "'1111'" in row.problem
    assert grade.check_never_say((("1111",),), [AssistantMessage(content="Invoice 11110 is fine.")]).passed


def test_communicate_matches_model_typography():
    """Curly apostrophes, non-breaking hyphens, dashes and markdown in the agent's text match plain-ASCII forms."""
    said = grade._plain("Redwood’s model tokenizer counts it; wait **60‑minute** windows (Aug 1–15), total $2,140.00.")
    for form in ("redwood's model tokenizer", "60-minute", "aug 1-15", "2140"):
        assert grade._says(form, said), form
    assert not grade._says("214", said)


def test_a_developer_who_says_the_answer_first_makes_the_case_invalid():
    from grade.grade import developer_leak
    from redwood_kit.customer import CustomerScript

    case = Scenario(id="t", communicate=(("2027-04-12", "April 12"),),
                    customer=CustomerScript(opening="Our payment failed.", agenda=("When will it go through?",)))

    def conversation(*turns):
        return Result(case_id="t", messages=[{"role": role, "content": text} for role, text in turns])

    leaked = conversation(("user", "Our payment failed."), ("assistant", "It is retried automatically."),
                          ("user", "Will it go through by April 12?"))
    echoed = conversation(("user", "Our payment failed."), ("assistant", "Retries finish by April 12."),
                          ("user", "Great, April 12 then."))
    assert developer_leak(case, leaked) is not None
    assert developer_leak(case, echoed) is None  # confirming what the agent said is not a leak
    scripted = case.model_copy(update={"customer": CustomerScript(opening="It was due April 12.")})
    assert developer_leak(scripted, conversation(("user", "It was due April 12."))) is None


def test_a_provider_failure_voids_only_its_own_case(monkeypatch, tmp_path):
    from grade import grade as grading
    from grade.scenarios import Step
    from run.provider import ProviderError
    from run.run_agent import Result

    def run_case(case, workspace, log_dir, customer, release):
        if case.id == "a0-02":
            raise ProviderError("RateLimitError: 429")
        return Result(case_id=case.id)

    monkeypatch.setattr(grading, "run_case", run_case)
    _, graded = grading.run_step(Step.BUILD_R0, tmp_path, tmp_path, workers=1,
                                 select=grading.Selection(only=frozenset({"a0-02", "a0-10"})))
    assert sorted(g.id for g in graded) == ["a0-02", "a0-10"]
    record = json.loads((tmp_path / "cases" / "a0-02.json").read_text())
    assert record["result"]["invalid"].startswith("the model provider failed")
    assert json.loads((tmp_path / "cases" / "a0-10.json").read_text())["result"]["invalid"] is None


def test_health_splits_agent_errors_by_kind_and_counts_invalid_cases():
    from evolve.diagnostics import ending
    from run.run_agent import Result

    assert ending(Result(case_id="t", termination="customer_stop")) == "customer_stop"
    assert ending(Result(case_id="t", error="RuntimeError: candidate process exited or timed out")) == \
        "agent_error: crash or timeout"
    assert ending(Result(case_id="t", error="RuntimeError: candidate error: Traceback ...")) == \
        "agent_error: agent exception"
    assert ending(Result(case_id="t", termination="max_steps")) == "max_steps"
    assert ending(Result(case_id="t", invalid="the model provider failed")) == "invalid"
