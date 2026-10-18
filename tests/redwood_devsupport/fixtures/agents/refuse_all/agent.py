"""Refuses every question. Every scenario needs something done, so this should score near 0."""
from redwood_kit.messages import AssistantMessage


class Agent:
    def get_init_state(self, message_history=None):
        return []

    def generate_next_message(self, message, state):
        return AssistantMessage(content="Sorry, I can't help with billing questions."), state


def create_agent():
    return Agent()
