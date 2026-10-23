"""Practice conversations run the graded loop, and the practice model service serves only what it should."""
from workspaces import copy_manifest, copy_starter  # noqa: E402
import itertools
import json
import sys
from pathlib import Path

import pytest

TASK = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment"), str(TASK / "environment" / "practice_model")]

from grade.scenarios import Scenario  # noqa: E402
from redwood_kit import conversation, customer, practice  # noqa: E402
from redwood_kit.billing import store  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, to_dict  # noqa: E402
from run import gateway, run_agent  # noqa: E402

import server  # noqa: E402

SCRIPT = customer.CustomerScript(
    opening="Our August 2026 invoice has the same $180 charge twice. Org alderwick-labs.",
    agenda=("What happens next?",), facts={"org_id": "alderwick-labs", "invoice_id": "PRACTICE-DUP-180"})


def agent_turns():
    escalate = ToolCall("c1", "escalate", {"account_id": "practice-alderwick", "team": "billing_ops",
                                           "summary": "Duplicate $180 line on PRACTICE-DUP-180"})
    ask = ToolCall("c0", "ask_developer", {"fields": ["invoice_id"]})
    return [AssistantMessage(tool_calls=[ask]), AssistantMessage(tool_calls=[escalate]),
            AssistantMessage(content="Billing Ops will follow up.")]


class FakeService:
    """The practice model service: scripted agent turns, and no wording (the line is said as written)."""

    def __init__(self, turns):
        self.turns, self.calls = turns, []

    def call(self, op, **fields):
        self.calls.append(op)
        if op == "say":
            return {"ok": True, "text": None}
        turn = self.turns.pop(0)
        turn.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return {"ok": True, "message": to_dict(turn)}

    def close(self):
        pass


class InProcessConsole:
    """A practice console connection, served in process exactly as the socket service serves one."""

    def __init__(self, seed):
        self.store, self.at = store.Billing(seed, release="R0"), itertools.count(1)

    def send(self, request):
        return store._reply(self.store, request, next(self.at))

    def close(self):
        pass


@pytest.fixture(autouse=True)
def local_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(practice, "KIT_PATH", str(TASK / "environment"))
    monkeypatch.setattr(practice, "MANIFEST", copy_manifest(tmp_path / "manifest.json"))
    if sys.platform != "linux":
        monkeypatch.setattr(practice, "LIMITED", False)  # macOS cannot cap address space; the container is Linux


def workspace(tmp_path):
    frozen = tmp_path / "frozen"
    conversation.freeze(copy_starter(tmp_path / "starter"), frozen)
    return frozen


def test_a_practice_conversation_is_the_graded_conversation(monkeypatch, tmp_path):
    seed = store.practice_seed("R0")
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    monkeypatch.setattr(run_agent, "load_db", lambda release: seed)
    graded_turns = agent_turns()

    def generate(model, messages, tools, tool_choice, **kwargs):
        turn = graded_turns.pop(0)
        turn.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return turn

    monkeypatch.setattr(gateway, "generate", generate)
    frozen = workspace(tmp_path)
    graded = run_agent.run_case(Scenario(id="t"), frozen, tmp_path, customer.Customer(SCRIPT))

    case = practice.PracticeCase(id="t", persona="brief", customer=SCRIPT)
    session = practice.PracticeSession(case, frozen, tmp_path, "R0", (FakeService(agent_turns()), InProcessConsole(seed)))
    session.run()
    session.close()

    assert (graded.termination, graded.error) == ("customer_transfer", None)
    assert (session.termination, session.error) == (graded.termination, graded.error)
    assert [to_dict(m) for m in session.messages] == graded.messages  # same turns, tool results and endings
    assert [c.model_dump() for c in session.customer.calls if c.op != "say"] == \
        [c.model_dump() for c in graded.developer_calls]
    assert session.handed_off() and session.console_calls[-1]["result"]["status"] == "escalated"


def test_a_service_failure_stops_the_run_instead_of_blaming_the_agent(tmp_path):

    class Down(FakeService):
        def call(self, op, **fields):
            if op == "generate":
                raise practice.PracticeServiceError("the practice budget of 600 generate calls for R0 is used up")
            return super().call(op, **fields)

    case = practice.PracticeCase(id="t", customer=SCRIPT)
    session = practice.PracticeSession(case, workspace(tmp_path), tmp_path, "R0",
                                       (Down([]), InProcessConsole(store.practice_seed("R0"))))
    with pytest.raises(practice.PracticeServiceError, match="budget"):
        session.run()
    session.close()


def test_the_example_scripts_load_and_name_only_practice_records():
    seeds = [store.practice_seed("R0"), store.practice_seed("R1", TASK / "environment" / "r1" / "development_seed.json")]
    known = {key for seed in seeds for rows in seed.values() for key in rows}
    orgs = {row["org"] for seed in seeds for row in seed["accounts"].values()}
    for path in (TASK / "environment" / "app" / "framework" / "client_api" / "practice_conversations.json",
                 TASK / "environment" / "r1" / "framework" / "client_api" / "practice_conversations.json"):
        for case in practice.load_cases(path):
            text = json.dumps(case.model_dump())
            assert case.customer.facts["org_id"] in orgs, case.id
            for word in text.replace('"', " ").replace(",", " ").split():
                if word.startswith(("PRACTICE-", "req_practice_")):
                    assert word.rstrip(".") in known, (case.id, word)


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LOG", tmp_path / "practice-model.log")
    monkeypatch.setattr(server, "BUDGET", server.Budget())
    monkeypatch.setattr(server, "CONVERSATIONS", 2)
    monkeypatch.setattr(server, "BUDGETS", {"generate": 3, "say": 1})
    return server


def test_the_service_checks_calls_like_grading_and_caps_conversations_per_release(service, monkeypatch):
    monkeypatch.setattr(gateway, "generate", lambda *a, **k: AssistantMessage(content="ok"))
    call = {"op": "generate", "release": "R0", "model": "openai/gpt-6-luna", "messages": [], "kwargs": {}}
    assert service.answer(call)["kind"] == "request"  # no conversation opened
    first = service.answer({"op": "open", "release": "R0"})["conversation"]
    call = {**call, "conversation": first}
    assert service.answer(call)["ok"]
    assert service.answer({**call, "model": "gpt-4o"})["kind"] == "request"  # not in the manifest
    assert service.answer({**call, "kwargs": {"temperature": 0}})["kind"] == "request"
    assert service.answer(call)["kind"] == "budget"  # 3 calls per conversation, rejected ones count
    assert service.answer({**call, "release": "R1"})["kind"] == "request"  # an R0 conversation is not R1's
    assert service.answer({"op": "open", "release": "R0"})["ok"]
    assert service.answer({"op": "open", "release": "R0"})["kind"] == "budget"  # 2 conversations per release
    assert service.answer({"op": "open", "release": "R1"})["ok"]  # R1 has its own
    assert service.answer({"op": "complete", "release": "R0"})["kind"] == "request"  # no general completion
    logged = [json.loads(line) for line in service.LOG.read_text().splitlines()]
    assert len(logged) == 11 and all("messages" not in entry for entry in logged)


def test_the_service_words_lines_with_the_grading_rules(service, monkeypatch):
    seen = []
    monkeypatch.setenv("USER_MODEL", "wording-model")
    monkeypatch.setattr(server.gateway, "complete",
                        lambda model, instructions, text, effort: seen.append((model, instructions, text, effort)) or "hi")
    conversation = service.answer({"op": "open", "release": "R0"})["conversation"]
    reply = service.answer({"op": "say", "release": "R0", "conversation": conversation, "persona": "brief",
                            "line": "Org acme.", "approval": "Yes, go ahead.", "transcript": "Support: Hi!"})
    assert reply == {"ok": True, "text": "hi"}
    assert seen == [("wording-model", customer.instructions("brief", "Org acme.", "Yes, go ahead."), "Support: Hi!", "low")]
    monkeypatch.delenv("USER_MODEL")
    other = service.answer({"op": "open", "release": "R1"})["conversation"]
    assert service.answer({"op": "say", "release": "R1", "conversation": other, "persona": "", "line": "x",
                           "approval": "", "transcript": ""}) == {"ok": True, "text": None}  # no wording model


def test_the_agent_clock_is_the_conversation_date(monkeypatch, tmp_path):
    library = tmp_path / "x86" / "faketime" / "libfaketime.so.1"
    library.parent.mkdir(parents=True)
    library.write_text("")
    monkeypatch.setattr(conversation, "FAKETIME_LIBRARY", str(tmp_path / "*" / "faketime" / "libfaketime.so.1"))
    env = conversation.clock_env("2026-09-15 13:20:00")
    assert env == {"LD_PRELOAD": str(library), "FAKETIME": "@2026-09-15 13:20:00",
                   "FAKETIME_DONT_FAKE_MONOTONIC": "1", "TZ": "UTC"}
    assert conversation.clock_env("") == {}  # no date asked for: the host clock
    monkeypatch.setattr(conversation, "FAKETIME_LIBRARY", str(tmp_path / "missing" / "*.so.1"))
    monkeypatch.setattr(conversation.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="libfaketime"):  # the image must never give the agent the host's date
        conversation.clock_env("2026-09-15 13:20:00")
    monkeypatch.setattr(conversation.sys, "platform", "darwin")
    assert conversation.clock_env("2026-09-15 13:20:00") == {}  # local runs off the image keep the host clock
    assert practice.PRACTICE_CLOCK == {"R0": "2026-09-15 12:00:00", "R1": "2027-04-08 12:00:00"}


def test_a_provider_failure_gives_the_practice_conversation_back(service, monkeypatch):
    def rate_limited(*args, **kwargs):
        raise server.ProviderError("RateLimitError: 429")

    monkeypatch.setattr(gateway, "generate", rate_limited)
    for _ in range(3):  # more failures than the 2-conversation budget: none of them count
        conversation = service.answer({"op": "open", "release": "R0"})["conversation"]
        reply = service.answer({"op": "generate", "release": "R0", "conversation": conversation,
                                "model": "openai/gpt-6-luna", "messages": [], "kwargs": {}})
        assert reply["kind"] == "provider" and "not counted" in reply["error"]
    assert service.answer({"op": "open", "release": "R0"})["ok"]
