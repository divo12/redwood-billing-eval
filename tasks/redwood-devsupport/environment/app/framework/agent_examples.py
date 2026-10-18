"""Agent engineering reference 1.0.0: synthetic kit self-check, no API calls.

Run in the task image: python /app/framework/agent_examples.py
Locally: PYTHONPATH=tasks/redwood-devsupport/environment python <this file>
Requires the kit's existing pydantic dependency. Not a deployed support agent.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from redwood_kit.agent_context import ModelConfig, ModelGateway
from redwood_kit.hooks import Blocked, HookKitBase, ToolRunner, hook
from redwood_kit.messages import AssistantMessage, ToolCall, to_dict
from redwood_kit.skills import load_skills
from redwood_kit.subagents import load_subagents, run_subagent
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

REFERENCE_VERSION = "1.0.0"


class DemoTools(ToolKitBase):
    def __init__(self):
        self.cards = {"demo-card": "Synthetic example: the blue cabinet holds spare notebooks."}
        self.notes: list[str] = []

    @is_tool(ToolType.READ)
    def read_card(self, card_id: str) -> dict:
        """Read one synthetic card by its exact identifier.

        Args:
            card_id: Identifier of the synthetic card to read.
        """
        if not isinstance(card_id, str) or not 1 <= len(card_id) <= 80:
            raise ValueError("card_id must be a string of 1 to 80 characters")
        if card_id not in self.cards:
            return {"found": False, "card_id": card_id}
        return {"found": True, "card_id": card_id, "text": self.cards[card_id]}

    @is_tool(ToolType.WRITE)
    def stage_note(self, text: str) -> dict:
        """Append a synthetic note in memory; it is not a ticket or external write.

        Args:
            text: Nonempty note of at most 200 characters.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise ValueError("text must be nonempty and at most 200 characters")
        self.notes.append(text)
        return {"note_number": len(self.notes)}


class DemoHooks(HookKitBase):
    """A synthetic workflow guard, not a security boundary for direct Python calls."""

    def __init__(self):
        self.closed = False

    @hook("pre_tool", match="stage_note")
    def require_open(self, tool: str, args: dict) -> None:
        if self.closed:
            raise Blocked("the synthetic note session is closed")


def demo() -> None:
    tools, hooks = DemoTools(), DemoHooks()
    runner = ToolRunner(tools, hooks)
    schema = next(t for t in tools.get_tools() if t.name == "read_card")
    assert schema.parameters["properties"]["card_id"]["type"] == "string"
    invalid = runner.run(ToolCall("bad", "read_card", {"card_id": 7}))
    assert invalid.id == "bad" and invalid.error  # schema alone did not validate
    missing = runner.run(ToolCall("missing", "read_card", {"card_id": "absent"}))
    assert not missing.error and json.loads(missing.content)["found"] is False
    empty = runner.run(ToolCall("empty", "stage_note", {"text": " "}))
    assert empty.error and tools.notes == []
    success = runner.run(ToolCall("write", "stage_note", {"text": "Synthetic note."}))
    assert not success.error and json.loads(success.content)["note_number"] == 1
    hooks.closed = True
    blocked = runner.run(ToolCall("blocked", "stage_note", {"text": "Another note."}))
    assert blocked.error and "Blocked:" in blocked.content and len(tools.notes) == 1

    # All files/data are synthetic and confined to an ephemeral directory.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        skill = root / "skills" / "inspect-card" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: inspect-card\ndescription: Inspect a synthetic card.\n---\n"
                         "Read the specified card; report the finding with its card ID.\n")
        delegate = root / "subagents" / "reader.md"
        delegate.parent.mkdir()
        delegate.write_text("---\nname: reader\ndescription: Read a synthetic card.\n"
                            "tools: read_card\nmax_turns: 3\n---\n"
                            "Report the supplied card's contents and ID.\n")
        assert "card ID" in load_skills(root)["inspect-card"].body
        spec = load_subagents(root)["reader"]
        calls: list[dict] = []

        def scripted_send(payload: dict) -> dict:
            assert payload["call"] == "model" and payload["model"] == "scripted-demo"
            assert [t["name"] for t in payload["tools"]] == ["read_card"]
            calls.append(payload)
            if len(calls) == 1:
                assert payload["messages"][-1]["content"] == "Read demo-card."
                reply = AssistantMessage(tool_calls=[ToolCall("read", "read_card", {"card_id": 7})])
            elif len(calls) == 2:
                result = payload["messages"][-1]["tool_messages"][0]
                assert result["id"] == "read" and result["error"]
                reply = AssistantMessage(tool_calls=[ToolCall("retry", "read_card", {"card_id": "demo-card"})])
            else:
                result = payload["messages"][-1]["tool_messages"][0]
                assert result["id"] == "retry" and not result["error"]
                assert json.loads(result["content"])["text"] == tools.cards["demo-card"]
                reply = AssistantMessage(content="demo-card: spare notebooks are in the blue cabinet.")
            return {"message": to_dict(reply)}

        gateway = ModelGateway((ModelConfig("scripted-demo"),), scripted_send)
        answer = run_subagent(spec, "Read demo-card.", gateway, runner)
        assert "demo-card" in answer and len(calls) == 3 and len(tools.notes) == 1
    print(f"Agent engineering reference {REFERENCE_VERSION}: synthetic self-check passed (no API calls).")


if __name__ == "__main__":
    demo()
