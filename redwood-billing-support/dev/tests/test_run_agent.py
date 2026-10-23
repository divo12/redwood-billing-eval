"""The verifier's conversation loop, with scripted model replies and a scripted customer."""
from workspaces import copy_starter  # noqa: E402
import shutil

import pytest
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from grade.scenarios import Scenario  # noqa: E402
from redwood_kit.messages import AssistantMessage, SystemMessage, ToolCall, ToolMessage, UserMessage  # noqa: E402
from run import (
    gateway,  # noqa: E402
    run_agent,  # noqa: E402
)


class ScriptedCustomer:
    def __init__(self, *turns: str, facts: dict | None = None, confirm: str = "yes") -> None:
        self.turns, self.heard, self.calls, self.requests = list(turns), [], [], []
        self.facts, self.confirm = facts or {}, confirm

    def reply(self, agent_text: str, handed_off: bool) -> UserMessage:
        self.heard.append(agent_text)
        return UserMessage(content=self.turns.pop(0))

    def handle(self, request: dict, order: int) -> dict:
        self.requests.append((request, order))
        if request["op"] == "ask":
            return {"answers": {f: self.facts.get(f, "I don't have that.") for f in request["fields"]}}
        return {"answer": self.confirm, "message": self.confirm}


def converse(monkeypatch, tmp_path, agent_turns, customer, files=None):
    calls = []  # the messages of every model call, agent's and subagents'

    def generate(model, messages, tools, tool_choice, **kwargs):
        calls.append(messages)
        turn = agent_turns.pop(0)
        turn.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return turn

    monkeypatch.setattr(gateway, "generate", generate)
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    workspace = tmp_path / "workspace"
    copy_starter(workspace)
    for name, text in (files or {}).items():
        (workspace / name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_text(text)
    return run_agent.run_case(Scenario(id="t"), workspace, tmp_path, customer), calls


def escalated(result):
    return [case for case in result.billing.state["cases"].values() if case["status"] == "escalated"]


def test_customer_stop_ends_the_conversation_after_tool_rounds(monkeypatch, tmp_path):
    escalate = ToolCall("c1", "escalate", {
        "account_id": "copperleaf-ai", "team": "billing_ops", "summary": "Duplicate line on INV-1",
    })
    customer = ScriptedCustomer("Some requests fail with 401.", "Which services?", "Thanks. ###STOP###")
    agent = [AssistantMessage(tool_calls=[escalate]), AssistantMessage(content="Escalated."),
             AssistantMessage(content="Check every service's key.")]
    result, calls = converse(monkeypatch, tmp_path, agent, customer)
    assert (result.termination, result.error) == ("customer_stop", None)
    assert result.seconds > 0
    assert len(calls) == 3 and not agent  # no agent call after the stop
    assert customer.heard == [run_agent.GREETING, "Escalated.", "Check every service's key."]
    assert '"status": "escalated"' in result.messages[3]["content"]
    [case] = escalated(result)
    assert (case["team"], case["summary"], case["at"]) == ("billing_ops", "Duplicate line on INV-1", 3)


def test_step_limit_ends_a_conversation_the_customer_never_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(run_agent, "MAX_STEPS", 2)
    customer = ScriptedCustomer("Help?", "Still broken.", "Still broken.")
    agent = [AssistantMessage(content="Try again."), AssistantMessage(content="Try again.")]
    result, _ = converse(monkeypatch, tmp_path, agent, customer)
    assert result.termination == "max_steps"


HARNESS = {
    "tools.py": '''
from redwood_kit.skills import SkillTools
from redwood_kit.subagents import SubagentTools
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

class Tools(ToolKitBase, SkillTools, SubagentTools):
    @is_tool(ToolType.WRITE)
    def escalate(self, summary: str) -> dict:
        """Hand off to an engineer.

        Args:
            summary: What is wrong.
        """
        return {"status": "queued"}
''',
    "hooks.py": '''
import re
from redwood_kit.hooks import Blocked, HookKitBase, hook

class Hooks(HookKitBase):
    @hook("user_message")
    def redact(self, text):
        return re.sub(r"\\d(?:[ -]?\\d){12,18}", "[card]", text)

    @hook("pre_tool", match="escalate")
    def only_humans_escalate(self, tool, args):
        raise Blocked("escalation needs a human")
''',
    "skills/card-data/SKILL.md": "---\nname: card-data\ndescription: A card number appears.\n---\nNever repeat it.",
    "subagents/reviewer.md": "---\nname: reviewer\ndescription: Reviews risky asks.\n"
                             "tools: escalate\n---\nYou review requests.",
}


def test_hooks_skills_and_subagents_run_through_the_kit(monkeypatch, tmp_path):
    customer = ScriptedCustomer("My card 4111 1111 1111 1111 was charged twice.", "Thanks. ###STOP###")
    agent = [
        AssistantMessage(tool_calls=[ToolCall("c1", "load_skill", {"name": "card-data"})]),
        AssistantMessage(tool_calls=[ToolCall("c2", "delegate", {"subagent": "reviewer", "task": "Refund?"})]),
        AssistantMessage(tool_calls=[ToolCall("s1", "escalate", {"summary": "refund"})]),  # the subagent
        AssistantMessage(content="Needs a human."),  # the subagent's answer
        AssistantMessage(content="A human will review the double charge."),
    ]
    result, calls = converse(monkeypatch, tmp_path, agent, customer, HARNESS)
    assert (result.termination, result.error) == ("customer_stop", None)
    assert "4111" not in str(calls[0]) and "[card]" in str(calls[0])  # redacted before the model saw it
    assert "Never repeat it." in result.messages[3]["content"]
    assert "Blocked: escalation needs a human" in str(calls[3])  # a hook held inside the subagent
    assert '"answer": "Needs a human."' in result.messages[5]["content"]


def test_is_stop_ends_the_conversation_after_its_tool_calls(monkeypatch, tmp_path):
    starter = (copy_starter(tmp_path / "starter") / "agent.py").read_text()
    stops = starter + "\nAgent.is_stop = lambda self, message: bool(message.tool_calls)\n"
    escalate = ToolCall("c1", "escalate", {
        "account_id": "copperleaf-ai", "team": "account_team", "summary": "Close the account",
    })
    customer = ScriptedCustomer("Please delete my account.")
    result, calls = converse(monkeypatch, tmp_path, [AssistantMessage(tool_calls=[escalate])],
                             customer, {"agent.py": stops})
    assert result.termination == "agent_stop" and len(calls) == 1
    assert customer.heard == [run_agent.GREETING]  # no developer turn after the stop
    assert '"status": "escalated"' in result.messages[-1]["content"]  # the closing tool call ran


def test_transfer_token_is_its_own_termination(monkeypatch, tmp_path):
    customer = ScriptedCustomer("Raise my rate limit now.", "OK, thanks. ###TRANSFER###")
    result, _ = converse(monkeypatch, tmp_path, [AssistantMessage(content="I've escalated this.")], customer)
    assert result.termination == "customer_transfer"


def _response(message, usage=None):
    import litellm
    return litellm.ModelResponse(choices=[{"index": 0, "message": message, "finish_reason": "stop"}],
                                 usage=usage or {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})


def test_generate_sends_chat_tools_and_turns(monkeypatch):
    sent = {}

    def completion(**request):
        sent.update(request)
        return _response({"role": "assistant", "content": "hi", "tool_calls": [
            {"id": "call_2", "type": "function", "function": {"name": "find_account", "arguments": '{"q": "x"}'}}]})

    monkeypatch.setattr(gateway.litellm, "completion", completion)
    tool = {"type": "function", "name": "find_account", "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []}}
    history = [SystemMessage("be nice"), AssistantMessage(content="Hi! How can I help?"), UserMessage(content="hello"),
               AssistantMessage(tool_calls=[ToolCall("call_1", "find_account", {})]),
               ToolMessage(id="call_1", content="{}")]
    reply = gateway.generate("openai/m", history, [tool], "required")
    assert (reply.content, reply.tool_calls) == ("hi", [ToolCall("call_2", "find_account", {"q": "x"})])
    assert reply.usage == {"prompt_tokens": 10, "completion_tokens": 3, "cache_write_tokens": 0, "cache_read_tokens": 0}
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user", "assistant", "tool"]
    assert sent["messages"][1]["content"] == gateway.OPENING  # a user turn before the greeting
    assert sent["messages"][4]["tool_calls"][0]["function"] == {"name": "find_account", "arguments": "{}"}
    assert sent["messages"][5] == {"role": "tool", "tool_call_id": "call_1", "content": "{}"}
    assert sent["tools"] == [{"type": "function", "function": {"name": "find_account", "description": "d",
                                                               "parameters": tool["parameters"]}}]
    assert sent["tool_choice"] == "required" and sent["reasoning_effort"] == "high"
    assert "max_tokens" not in sent and "cache_control_injection_points" not in sent  # provider defaults
    gateway.generate("anthropic/m", history, None, None, max_output_tokens=50)
    assert sent["max_tokens"] == 50 and sent["cache_control_injection_points"] == gateway.CACHE_POINTS


def test_the_reasoning_effort_is_configurable(monkeypatch):
    sent = {}
    monkeypatch.setattr(gateway.litellm, "completion",
                        lambda **request: sent.update(request) or _response({"role": "assistant", "content": "ok"}))
    monkeypatch.setenv(gateway.EFFORT, "low")
    gateway.generate("openai/m", [UserMessage(content="hi")], None, None)
    assert sent["reasoning_effort"] == "low"
    monkeypatch.setenv(gateway.EFFORT, "")
    sent.clear()
    gateway.generate("openai/m", [UserMessage(content="hi")], None, None)
    assert "reasoning_effort" not in sent


def _litellm_error(kind, message):
    return kind(message=message, llm_provider="openai", model="m")


@pytest.mark.parametrize("error, provider", [
    (lambda: _litellm_error(gateway.BadRequestError, "Your credit balance is too low"), True),
    (lambda: _litellm_error(gateway.RateLimitError, "slow down"), True),
    (lambda: _litellm_error(gateway.InternalServerError, "overloaded"), True),
    (lambda: _litellm_error(gateway.BadRequestError, "tools.0.name: invalid"), False),
])
def test_provider_failures_invalidate_the_run_but_bad_agent_requests_do_not(monkeypatch, error, provider):
    def completion(**request):
        raise error()

    monkeypatch.setattr(gateway.litellm, "completion", completion)
    expected = gateway.ProviderError if provider else gateway.BadRequestError
    with pytest.raises(expected):
        gateway.generate("openai/m", [UserMessage(content="hi")], None, None)


def test_a_provider_failure_escapes_the_conversation_instead_of_scoring_it(monkeypatch, tmp_path):
    def generate(model, messages, tools, tool_choice, **kwargs):
        raise gateway.ProviderError("credit balance is too low")

    monkeypatch.setattr(gateway, "generate", generate)
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    workspace = tmp_path / "workspace"
    copy_starter(workspace)
    with pytest.raises(gateway.ProviderError):
        run_agent.run_case(Scenario(id="t"), workspace, tmp_path, ScriptedCustomer("Help.", "OK. ###STOP###"))


def test_a_replay_hint_reaches_the_system_message_only_when_set(monkeypatch):
    messages = [SystemMessage(content="You are support."), UserMessage(content="Hi")]
    monkeypatch.delenv(run_agent.REPLAY_HINT, raising=False)
    assert run_agent.with_replay_hint(messages) == messages  # graded runs: the messages as sent
    monkeypatch.setenv(run_agent.REPLAY_HINT, "Retries complete 3 business days after the window ends.")
    hinted = run_agent.with_replay_hint(messages)
    assert hinted[0].content.startswith("You are support.\n\nGuidance from the support team")
    assert hinted[0].content.endswith("Retries complete 3 business days after the window ends.")
    assert hinted[1:] == messages[1:] and messages[0].content == "You are support."  # the original is untouched
    bare = run_agent.with_replay_hint([UserMessage(content="Hi")])
    assert isinstance(bare[0], SystemMessage) and "3 business days" in bare[0].content
