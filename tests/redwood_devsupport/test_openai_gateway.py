"""The OpenAI override sends the configured reasoning effort unless the agent set its own. No API calls."""
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "tests"), str(TASK / "environment")]

from redwood_kit.messages import SystemMessage, UserMessage  # noqa: E402
from run import openai_gateway  # noqa: E402

MESSAGES = [SystemMessage(content="be brief"), UserMessage(content="hi")]


def test_effort_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(openai_gateway.EFFORT, "high")
    assert openai_gateway._request("m", MESSAGES, None, None, {})["reasoning"] == {"effort": "high"}


def test_agent_reasoning_wins_and_unset_sends_none(monkeypatch):
    monkeypatch.setenv(openai_gateway.EFFORT, "high")
    request = openai_gateway._request("m", MESSAGES, None, None, {"reasoning": {"effort": "low"}})
    assert request["reasoning"] == {"effort": "low"}
    monkeypatch.delenv(openai_gateway.EFFORT)
    assert "reasoning" not in openai_gateway._request("m", MESSAGES, None, None, {})
