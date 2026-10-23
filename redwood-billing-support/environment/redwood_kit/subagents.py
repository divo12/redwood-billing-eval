"""Load Markdown subagents and run them through the shared model and tool runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_context import ModelGateway
from .hooks import ToolRunner
from .messages import AssistantMessage, Message, MultiToolMessage, SystemMessage, UserMessage
from .skills import frontmatter, workspace_of
from .toolkit import Tool, ToolType, is_tool


@dataclass(frozen=True)
class Subagent:
    name: str
    description: str
    prompt: str
    model: str | None
    tools: tuple[str, ...]
    max_turns: int


def load_subagents(workspace: Path) -> dict[str, Subagent]:
    subagents: dict[str, Subagent] = {}
    for path in sorted(Path(workspace, "subagents").glob("*.md")):
        meta, body = frontmatter(path.read_text())
        name = meta.get("name") or path.stem
        tools = tuple(t.strip() for t in meta.get("tools", "").split(",") if t.strip())
        subagents[name] = Subagent(name, meta.get("description", ""), body, meta.get("model"),
                                   tools, int(meta.get("max_turns", 8)))
    return subagents


def run_subagent(spec: Subagent, task: str, gateway: ModelGateway, runner: ToolRunner) -> str:
    """Loop the subagent's model and tools until it answers with text."""
    tools = _allowed_tools(spec, runner)
    messages: list[Message] = [SystemMessage(spec.prompt), UserMessage(content=task)]
    for _ in range(spec.max_turns):
        reply = _next_reply(spec, gateway, messages, tools)
        messages.append(reply)
        if not reply.is_tool_call():
            return reply.content or ""
        messages.append(MultiToolMessage([runner.run(call) for call in reply.tool_calls or []]))
    raise RuntimeError(f"subagent {spec.name} did not answer within {spec.max_turns} turns")


def _allowed_tools(spec: Subagent, runner: ToolRunner) -> list[Tool]:
    return [tool for tool in runner.toolkit.get_tools() if tool.name in spec.tools]


def _next_reply(
    spec: Subagent,
    gateway: ModelGateway,
    messages: list[Message],
    tools: list[Tool],
) -> AssistantMessage:
    model = spec.model or gateway.models[0].model
    return gateway.generate(model=model, messages=messages, actions=tools or None)


class SubagentTools:
    """Mix into `Tools` to give the model a `delegate` tool over `workspace/subagents/`.

    The runtime sets `self.gateway` and `self.runner` before any tool runs.
    """

    gateway: ModelGateway
    runner: ToolRunner

    @is_tool(ToolType.GENERIC)
    def delegate(self, subagent: str, task: str) -> dict[str, str]:
        """Hand a self-contained task to a specialist subagent and get its answer.

        Args:
            subagent: Name of a subagent listed in your instructions.
            task: Everything the subagent needs; it sees nothing else of the conversation.
        """
        subagents = load_subagents(workspace_of(self))
        if subagent not in subagents:
            raise ValueError(f"unknown subagent {subagent!r}; available: {sorted(subagents)}")
        return {"subagent": subagent, "answer": run_subagent(subagents[subagent], task, self.gateway, self.runner)}
