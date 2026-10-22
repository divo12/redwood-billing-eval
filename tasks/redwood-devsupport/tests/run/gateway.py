"""The verifier's side of the model gateway: Anthropic for the agent's turns, a local model for embeddings.

`generate` turns conversation messages into one Messages API completion; `embed` returns vectors in
input order. The allowed models mirror environment/app/framework/deployment_manifest.json.
"""

from __future__ import annotations

import functools
import os
from typing import cast

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from anthropic.types import Message as AnthropicMessage
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from redwood_kit.agent_context import ModelConfig
from redwood_kit.embeddings import encode
from redwood_kit.jsontypes import Json, JsonObject
from redwood_kit.messages import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from run import openai_gateway
from run.provider import ProviderError

# Mirror environment/app/framework/deployment_manifest.json, which the builder can read but not change.
MODELS = (ModelConfig("claude-sonnet-5-5"),)
EMBEDDING_MODELS = (ModelConfig("sentence-transformers/all-MiniLM-L6-v2"),)
ALLOWED_KWARGS = {"max_output_tokens"}
EMBED_KWARGS: set[str] = set()
DEFAULT_MAX_TOKENS = 4096
# Testing only: "openai:<model>" serves the agent's calls with that OpenAI model (run/openai_gateway.py).
OVERRIDE = "REDWOOD_SUPPORT_OVERRIDE"
EPHEMERAL: JsonObject = {"type": "ephemeral"}  # Anthropic prompt caching: repeated prefixes bill at ~0.1x
# The Messages API needs a user turn first; the conversation opens with the agent's greeting.
OPENING = "(The developer has opened a billing support chat.)"


ACCOUNT_PROBLEMS = ("credit balance",)  # billing problems the API reports as a 400


def _provider_failure(error: APIStatusError | APIConnectionError) -> bool:
    if isinstance(error, (APIConnectionError, AuthenticationError, PermissionDeniedError, RateLimitError,
                          InternalServerError)):
        return True
    return isinstance(error, BadRequestError) and any(text in str(error) for text in ACCOUNT_PROBLEMS)


def json_list(items: list[Json] | list[JsonObject] | list[float] | list[list[Json]]) -> list[Json]:
    return list(items)


@functools.cache
def _anthropic() -> Anthropic:
    """One shared client, created on first use; reads ANTHROPIC_API_KEY."""
    return Anthropic(timeout=300.0, max_retries=3)


def _assistant_blocks(message: AssistantMessage) -> list[JsonObject]:
    blocks: list[JsonObject] = [{"type": "text", "text": message.content}] if message.content else []
    return blocks + [{"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                     for call in message.tool_calls or []]


def _blocks(message: Message) -> tuple[str, list[JsonObject]] | None:
    """One message as (role, content blocks); None for an empty turn."""
    if isinstance(message, UserMessage):
        return ("user", [{"type": "text", "text": message.content}]) if message.content else None
    if isinstance(message, AssistantMessage):
        blocks = _assistant_blocks(message)
        return ("assistant", blocks) if blocks else None
    tools = [message] if isinstance(message, ToolMessage) else _multi_tools(message)
    return ("user", [_tool_result(tool) for tool in tools]) if tools else None


def _multi_tools(message: Message) -> list[ToolMessage]:
    return message.tool_messages if isinstance(message, MultiToolMessage) else []


def _tool_result(message: ToolMessage) -> JsonObject:
    return {"type": "tool_result", "tool_use_id": message.id, "content": message.content or "",
            "is_error": message.error}


def _to_input(messages: list[Message]) -> tuple[list[Json], str | None]:
    """Messages API turns (same-role neighbours merged, user first), plus the system prompt."""
    system = next((m.content for m in messages if isinstance(m, SystemMessage)), None)
    turns: list[JsonObject] = []
    for role, blocks in filter(None, map(_blocks, messages)):
        if turns and turns[-1]["role"] == role:
            previous = turns[-1]["content"]
            assert isinstance(previous, list)
            previous.extend(blocks)
        else:
            turns.append({"role": role, "content": json_list(blocks)})
    if turns and turns[0]["role"] == "assistant":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": OPENING}]})
    return json_list(turns), system


def _tool_choice(choice: Json) -> JsonObject:
    """OpenAI-style tool_choice as a Messages API one. The deployed model rejects forced choices
    ("required" or a named tool), so those become "auto"; "none" is kept."""
    return {"type": "none"} if choice == "none" else {"type": "auto"}


def _tools(tools: list[JsonObject]) -> list[Json]:
    """The runtime's function schemas as Messages API tools."""
    return [{"name": tool["name"], "description": tool.get("description") or "",
             "input_schema": tool.get("parameters") or {"type": "object", "properties": {}}} for tool in tools]


def _request_payload(model: str, messages: list[Message], tools: list[JsonObject] | None,
                     tool_choice: Json | None, kwargs: JsonObject) -> JsonObject:
    turns, system = _to_input(messages)
    max_tokens = kwargs.get("max_output_tokens")
    request: JsonObject = {"model": model, "messages": turns,
                           "max_tokens": max_tokens if isinstance(max_tokens, int) else DEFAULT_MAX_TOKENS}
    if system:  # breakpoint 1: tools render before system, so this caches tools + system prompt
        request["system"] = [{"type": "text", "text": system, "cache_control": EPHEMERAL}]
    _cache_conversation(turns)
    if tools:
        request["tools"] = _tools(tools)
        request["tool_choice"] = _tool_choice(tool_choice)
    return request


def _text(response: AnthropicMessage) -> str | None:
    return "\n".join(block.text for block in response.content
                     if block.type == "text" and block.text.strip()) or None


def _calls(response: AnthropicMessage) -> list[ToolCall] | None:
    calls = [ToolCall(block.id, block.name, cast(JsonObject, block.input)) for block in response.content
             if block.type == "tool_use" and isinstance(block.input, dict)]
    return calls or None


def _usage(response: AnthropicMessage) -> dict[str, int]:
    usage = response.usage
    written, read = usage.cache_creation_input_tokens or 0, usage.cache_read_input_tokens or 0
    return {"prompt_tokens": usage.input_tokens + written + read, "completion_tokens": usage.output_tokens,
            "cache_write_tokens": written, "cache_read_tokens": read}


def _cache_conversation(turns: list[Json]) -> None:
    """Breakpoint 2: the last block of the latest turn, so the next call re-reads the conversation from cache."""
    last = turns[-1] if turns else None
    content = last.get("content") if isinstance(last, dict) else None
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1] = {**content[-1], "cache_control": EPHEMERAL}


def _parse_response(response: AnthropicMessage) -> AssistantMessage:
    return AssistantMessage(content=_text(response), tool_calls=_calls(response), usage=_usage(response))


def _create_params(request: JsonObject) -> MessageCreateParamsNonStreaming:
    """Wire JSON as the SDK request type; the provider validates it (the SDK TypedDicts are stricter than the API)."""
    return cast(MessageCreateParamsNonStreaming, request)


def _send(request: JsonObject) -> AnthropicMessage:
    """One provider call; the provider's own failures become ProviderError, the agent's bad requests stay errors."""
    try:
        return _anthropic().messages.create(**_create_params(request))
    except (APIStatusError, APIConnectionError) as error:
        if _provider_failure(error):
            raise ProviderError(f"{type(error).__name__}: {error}") from error
        raise


def generate(model: str, messages: list[Message], tools: list[JsonObject] | None,
             tool_choice: Json | None, **kwargs: Json) -> AssistantMessage:
    """One Messages API completion; empty answers are asked again up to three times."""
    override = os.environ.get(OVERRIDE, "")
    if override.startswith("openai:"):
        return openai_gateway.generate(override.removeprefix("openai:"), messages, tools, tool_choice, dict(kwargs))
    request = _request_payload(model, messages, tools, tool_choice, kwargs)
    for _ in range(3):
        message = _parse_response(_send(request))
        if message.content or message.tool_calls:
            return message
    raise RuntimeError(f"{model} returned an empty completion three times")


def embed(model: str, inputs: list[str], **kwargs: Json) -> tuple[list[list[float]], int]:
    """Vectors in input order from the local model, and the (zero) provider tokens they cost."""
    del kwargs  # EMBED_KWARGS is empty: run_agent refuses any argument before this is called
    return encode(model, inputs), 0
