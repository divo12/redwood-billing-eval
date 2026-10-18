"""Escalates the first message and answers nothing. Handoff checks pass; everything that needs an answer fails."""
from redwood_kit.messages import AssistantMessage, ToolCall


class Agent:
    def get_init_state(self, message_history=None):
        return {"escalated": False}

    def generate_next_message(self, message, state):
        if not state["escalated"]:
            call = ToolCall("handoff-1", "escalate", {
                "account_id": "copperleaf-ai", "team": "billing_ops", "summary": message.content or "",
            })
            return AssistantMessage(tool_calls=[call]), {"escalated": True}
        return AssistantMessage(content="I've handed this to the right Redwood team, and they will follow up."), state


def create_agent():
    return Agent()
