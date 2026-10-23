from pathlib import Path

from redwood_kit.agent_context import get_agent_context
from redwood_kit.messages import SystemMessage
from redwood_kit.skills import load_skills
from redwood_kit.subagents import load_subagents

HERE = Path(__file__).parent
MODEL = "openai/gpt-6-luna"


class Agent:
    def __init__(self, context):
        self.gateway = context.model_gateway
        self.tools = context.action_interface.available
        instructions = (HERE / "system_prompt.md").read_text().strip()
        for heading, name in (("Standing instructions", "AGENTS.md"),):
            if text := (HERE / name).read_text().strip():
                instructions += f"\n\n# {heading}\n\n{text}"
        if skills := load_skills(HERE):
            lines = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills.values())
            instructions += f"\n\n# Skills\n\nCall load_skill with the name when one applies.\n\n{lines}"
        if subagents := load_subagents(HERE):
            lines = "\n".join(f"- {spec.name}: {spec.description}" for spec in subagents.values())
            instructions += f"\n\n# Subagents\n\nCall delegate with the name and a self-contained task.\n\n{lines}"
        self.system = SystemMessage(instructions)

    def get_init_state(self, message_history=None):
        return list(message_history or [])

    def generate_next_message(self, message, state):
        state = [*state, message]
        reply = self.gateway.generate(
            model=MODEL,
            messages=[self.system, *state],
            actions=self.tools or None,
        )
        return reply, [*state, reply]


def create_agent():
    return Agent(get_agent_context())
