"""The grader's decisions, with fake transcripts and a fake judge; no API calls."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
FIXTURES = Path(__file__).parent / "fixtures" / "agents"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from grade import (
    grade,  # noqa: E402
    judge,  # noqa: E402
)
from grade.grade import ScenarioGrade, check_actions  # noqa: E402
from grade.judge import Answer, JudgeAnswer, render  # noqa: E402
from grade.scenarios import Action, DbExpectation, Purpose, Scenario, Step, load_scenarios  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, ToolMessage, UserMessage  # noqa: E402
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


def test_the_judge_sees_roles_tool_calls_and_clipped_text():
    text = render([UserMessage("x" * 5000), call("escalate"), ToolMessage("c1", "done"),
                   AssistantMessage(content="Opened a ticket.")])
    assert text.startswith("DEVELOPER: " + "x" * 4000 + " [...]")
    assert 'AGENT TOOL CALL: escalate({"summary": "refund"})' in text
    assert "TOOL RESULT: done" in text and text.endswith("AGENT: Opened a ticket.")


CASE = Scenario.model_validate({"id": "c", "actions": [{"op": "write:cases", "contains": ["INV-1"]}], "checks": [
    {"judge": "Does the agent explain the lag?", "expect": True},
    {"judge": "Does the agent promise a refund?", "expect": False},
    {"judge": "Does the agent answer first?", "expect": True, "scored": False},
]})
ESCALATED = BillingRecord(log=(wrote("INV-1"),))


def result(termination=Termination.CUSTOMER_STOP, billing=BillingRecord()):
    return Result(case_id="c", termination=termination, billing=billing)


def answers(*yes_no):
    def ask(_transcript, _questions, _instructions):
        return [JudgeAnswer(n=n, answer=Answer(a), quote="AGENT: ok" if a == "yes" else "")
                for n, a in enumerate(yes_no, 1)]
    return ask


def test_a_scenario_passes_only_when_every_scored_check_passes():
    assert grade.grade(CASE, result(Termination.CUSTOMER_TRANSFER, ESCALATED), answers("yes", "no", "no")).passed
    promised = grade.grade(CASE, result(Termination.CUSTOMER_TRANSFER, ESCALATED), answers("yes", "yes", "yes"))
    assert not promised.passed and [r.passed for r in promised.checks] == [True, False, True, True]
    assert not grade.grade(CASE, result(), answers("yes", "no", "yes")).passed  # no handoff


def test_an_unfinished_conversation_fails_without_asking_the_judge():
    def never(*_):
        raise AssertionError("the judge was asked")
    for termination in (t for t in Termination if not t.finished):
        assert not grade.grade(CASE, result(termination, ESCALATED), never).passed


class FakeResponses:
    def __init__(self, *outputs):
        self.outputs, self.requests = list(outputs), []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=json.dumps({"answers": self.outputs.pop(0)}))


def fake_client(monkeypatch, *outputs):
    responses = FakeResponses(*outputs)
    monkeypatch.setattr(judge, "_openai", lambda: SimpleNamespace(responses=responses))
    return responses


def parsed(raw):
    return [JudgeAnswer.model_validate(answer) for answer in raw]


def test_the_judge_is_asked_again_when_a_yes_quotes_text_that_is_not_there(monkeypatch):
    invented = [{"n": 1, "answer": "yes", "quote": "your refund is approved"}]
    real = [{"n": 1, "answer": "yes", "quote": "I opened a  ticket"}]  # whitespace and case are normalized
    responses = fake_client(monkeypatch, invented, real)
    assert judge.ask_judge("AGENT: I opened a ticket.", ["Did it open a ticket?"]) == parsed(real)
    assert len(responses.requests) == 2 and responses.requests[0]["model"] == judge.JUDGE_MODEL


def test_a_retry_tells_the_judge_why_its_answers_were_rejected(monkeypatch):
    invented = [{"n": 1, "answer": "yes", "quote": "your refund is approved"}]
    real = [{"n": 1, "answer": "yes", "quote": "I opened a ticket"}]
    responses = fake_client(monkeypatch, invented, real)
    judge.ask_judge("AGENT: I opened a ticket.", ["Did it open a ticket?"])
    assert "rejected" not in responses.requests[0]["input"]
    assert "your refund is approved" in responses.requests[1]["input"]


def test_an_api_error_backs_off_and_asks_again(monkeypatch):
    real = [{"n": 1, "answer": "no", "quote": ""}]
    responses = fake_client(monkeypatch, real)
    create, calls, waits = responses.create, [], []

    def flaky(**request):
        calls.append(request)
        if len(calls) == 1:
            raise TimeoutError("judge timed out")
        return create(**request)

    responses.create = flaky
    monkeypatch.setattr(judge.time, "sleep", waits.append)
    assert judge.ask_judge("AGENT: hi", ["Q?"]) == parsed(real)
    assert waits == [judge.JUDGE_BACKOFF_SECONDS]


def test_quotes_match_on_words_despite_markdown_and_typographic_punctuation(monkeypatch):
    quote = [{"n": 1, "answer": "yes", "quote": "AWS Marketplace bills you - Redwood doesn't send an invoice"}]
    fake_client(monkeypatch, quote)
    transcript_ = "AGENT: **AWS Marketplace** bills you — Redwood doesn’t send an invoice."
    assert judge.ask_judge(transcript_, ["Q?"]) == parsed(quote)
    fake_client(monkeypatch, *[[{"n": 1, "answer": "yes", "quote": "Redwood sends an invoice"}]] * judge.JUDGE_ATTEMPTS)
    with pytest.raises(judge.JudgeError, match="question 1: 'Redwood sends an invoice'"):
        judge.ask_judge(transcript_, ["Q?"])


def test_a_judge_that_keeps_failing_makes_the_run_invalid(monkeypatch):
    fake_client(monkeypatch, *[[{"n": 2, "answer": "no", "quote": ""}]] * judge.JUDGE_ATTEMPTS)
    with pytest.raises(judge.JudgeError, match="answered questions"):
        judge.ask_judge("AGENT: hi", ["Q?"])


def test_rewordings_are_averaged_before_situations():
    scenarios = [Scenario(id="a", category="normal", rules=("BILL-1",)),
                 Scenario(id="b", realization_of="a", category="normal", rules=("BILL-1",)),
                 Scenario(id="c", category="exception", purpose=Purpose.UPDATE, rules=("BILL-2",))]
    graded = [ScenarioGrade(id=i, passed=p, termination=Termination.CUSTOMER_STOP, checks=[])
              for i, p in (("a", True), ("b", False), ("c", True))]
    summary = grade.summarize(scenarios, graded)
    assert summary.reward == 0.75 and summary.situations == 2
    assert summary.by_rule == {"BILL-1": "1/2", "BILL-2": "1/1"}


def test_every_scenario_needs_the_agent_to_do_something():
    for step in Step:
        for case in load_scenarios(step):
            db = case.db or DbExpectation()
            writes = any(action["op"] in {"insert", "update"}
                         for actions in (db.gold, *db.alternatives) for action in actions)
            assert writes or any(c.expect for c in case.checks if c.scored), case.id


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
