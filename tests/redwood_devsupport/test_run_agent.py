"""The verifier's conversation loop, with scripted model replies and a scripted customer."""
import shutil
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from grade.scenarios import Scenario  # noqa: E402
from redwood_kit.messages import AssistantMessage, ToolCall, UserMessage  # noqa: E402
from run import (
    gateway,  # noqa: E402
    run_agent,  # noqa: E402
)


class ScriptedCustomer:
    def __init__(self, *turns: str) -> None:
        self.turns, self.heard = list(turns), []

    def reply(self, agent_text: str) -> UserMessage:
        self.heard.append(agent_text)
        return UserMessage(content=self.turns.pop(0))


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


def test_generate_sends_function_tools_without_strict_unchanged(monkeypatch):
    sent = {}

    class Responses:
        def create(self, **request):
            sent.update(request)
            return gateway.Response.model_validate({
                "id": "r", "created_at": 0, "model": "m", "object": "response", "parallel_tool_calls": True,
                "tool_choice": "auto", "tools": [],
                "output": [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "hi", "annotations": []}]}],
            })

    class Client:
        responses = Responses()

    monkeypatch.setattr(gateway, "_openai", lambda: Client())
    tool = {"type": "function", "name": "find_account", "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []}}
    reply = gateway.generate("m", [UserMessage(content="hello")], [tool], None)
    assert reply.content == "hi"
    assert sent["tools"] == [tool]
