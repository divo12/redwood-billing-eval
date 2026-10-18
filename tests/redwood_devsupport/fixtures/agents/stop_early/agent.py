"""Ends every conversation after one polite reply. Expect-false checks pass vacuously; the scenario must not."""
from redwood_kit.messages import AssistantMessage


class Agent:
    def get_init_state(self, message_history=None):
        return []

    def generate_next_message(self, message, state):
        return AssistantMessage(content="Thanks for reaching out! Have a great day."), state

    def is_stop(self, message):
        return True


def create_agent():
    return Agent()
