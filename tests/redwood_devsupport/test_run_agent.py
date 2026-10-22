"""The verifier's conversation loop, with scripted model replies and a scripted customer."""
import shutil

import pytest
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
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
    shutil.copytree(TASK / "environment" / "app" / "workspace", workspace)
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
    starter = (TASK / "environment" / "app" / "workspace" / "agent.py").read_text()
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


def test_generate_sends_messages_api_tools_and_turns(monkeypatch):
    sent = {}

    class Messages:
        def create(self, **request):
            sent.update(request)
            return gateway.AnthropicMessage.model_validate({
                "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": "hi"},
                            {"type": "tool_use", "id": "toolu_2", "name": "find_account", "input": {"q": "x"}}],
                "stop_reason": "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 3}})

    class Client:
        messages = Messages()

    monkeypatch.setattr(gateway, "_anthropic", lambda: Client())
    tool = {"type": "function", "name": "find_account", "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []}}
    history = [SystemMessage("be nice"), AssistantMessage(content="Hi! How can I help?"), UserMessage(content="hello"),
               AssistantMessage(tool_calls=[ToolCall("toolu_1", "find_account", {})]),
               ToolMessage(id="toolu_1", content="{}")]
    reply = gateway.generate("claude-sonnet-5-5", history, [tool], "required")
    assert (reply.content, reply.tool_calls) == ("hi", [ToolCall("toolu_2", "find_account", {"q": "x"})])
    assert reply.usage == {"prompt_tokens": 10, "completion_tokens": 3, "cache_write_tokens": 0,
                           "cache_read_tokens": 0}
    assert sent["system"] == [{"type": "text", "text": "be nice", "cache_control": {"type": "ephemeral"}}]
    assert sent["tools"] == [{"name": "find_account", "description": "d", "input_schema": tool["parameters"]}]
    assert sent["tool_choice"] == {"type": "auto"}  # the model rejects forced choices
    assert [turn["role"] for turn in sent["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    assert sent["messages"][0]["content"][0]["text"] == gateway.OPENING
    assert sent["messages"][4]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}",
                                               "is_error": False, "cache_control": {"type": "ephemeral"}}]
    assert all("cache_control" not in block for turn in sent["messages"][:4] for block in turn["content"])


def _status_error(kind, status, message):
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return kind(message, response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize("error, provider", [
    (lambda: _status_error(gateway.BadRequestError, 400, "Your credit balance is too low"), True),
    (lambda: _status_error(gateway.RateLimitError, 429, "slow down"), True),
    (lambda: _status_error(gateway.InternalServerError, 529, "overloaded"), True),
    (lambda: _status_error(gateway.BadRequestError, 400, "tools.0.name: invalid"), False),
])
def test_provider_failures_invalidate_the_run_but_bad_agent_requests_do_not(monkeypatch, error, provider):
    class Messages:
        def create(self, **request):
            raise error()

    class Client:
        messages = Messages()

    monkeypatch.setattr(gateway, "_anthropic", lambda: Client())
    expected = gateway.ProviderError if provider else gateway.BadRequestError
    with pytest.raises(expected):
        gateway.generate("claude-sonnet-5-5", [UserMessage(content="hi")], None, None)


def test_a_provider_failure_escapes_the_conversation_instead_of_scoring_it(monkeypatch, tmp_path):
    def generate(model, messages, tools, tool_choice, **kwargs):
        raise gateway.ProviderError("credit balance is too low")

    monkeypatch.setattr(gateway, "generate", generate)
    monkeypatch.setattr(run_agent, "KIT_PATH", str(TASK / "environment"))
    workspace = tmp_path / "workspace"
    shutil.copytree(TASK / "environment" / "app" / "workspace", workspace)
    with pytest.raises(gateway.ProviderError):
        run_agent.run_case(Scenario(id="t"), workspace, tmp_path, ScriptedCustomer("Help.", "OK. ###STOP###"))
