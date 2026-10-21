"""The verifier's side of the model gateway: Anthropic for the agent's turns, OpenAI for embeddings.

`generate` turns conversation messages into one Messages API completion; `embed` returns vectors in
input order. The allowed models mirror environment/app/framework/deployment_manifest.json.
"""

from __future__ import annotations

import functools
from typing import cast

from anthropic import Anthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from openai import OpenAI, omit
from redwood_kit.agent_context import ModelConfig
from redwood_kit.embeddings import PROVIDER_BATCH
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

# Mirror environment/app/framework/deployment_manifest.json, which the builder can read but not change.
MODELS = (ModelConfig("claude-opus-5-5"),)
EMBEDDING_MODELS = (ModelConfig("text-embedding-3-small"), ModelConfig("text-embedding-3-large"))
ALLOWED_KWARGS = {"max_output_tokens"}
EMBED_KWARGS = {"dimensions"}
DEFAULT_MAX_TOKENS = 4096
# The Messages API needs a user turn first; the conversation opens with the agent's greeting.
OPENING = "(The developer has opened a billing support chat.)"


def json_list(items: list[Json] | list[JsonObject] | list[float] | list[list[Json]]) -> list[Json]:
    return list(items)


@functools.cache
def _anthropic() -> Anthropic:
    """One shared client, created on first use; reads ANTHROPIC_API_KEY."""
    return Anthropic(timeout=300.0, max_retries=3)


@functools.cache
def _openai() -> OpenAI:
    """One shared client, created on first use; reads OPENAI_API_KEY."""
    return OpenAI(timeout=300.0, max_retries=3)


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
    if system:
        request["system"] = system
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
    cached = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
    return {"prompt_tokens": usage.input_tokens + cached, "completion_tokens": usage.output_tokens}


def _parse_response(response: AnthropicMessage) -> AssistantMessage:
    return AssistantMessage(content=_text(response), tool_calls=_calls(response), usage=_usage(response))


def _create_params(request: JsonObject) -> MessageCreateParamsNonStreaming:
    """Wire JSON as the SDK request type; the provider validates it (the SDK TypedDicts are stricter than the API)."""
    return cast(MessageCreateParamsNonStreaming, request)


def generate(model: str, messages: list[Message], tools: list[JsonObject] | None,
             tool_choice: Json | None, **kwargs: Json) -> AssistantMessage:
    """One Messages API completion; empty answers are asked again up to three times."""
    request = _request_payload(model, messages, tools, tool_choice, kwargs)
    for _ in range(3):
        message = _parse_response(_anthropic().messages.create(**_create_params(request)))
        if message.content or message.tool_calls:
            return message
    raise RuntimeError(f"{model} returned an empty completion three times")


def embed(model: str, inputs: list[str], **kwargs: Json) -> tuple[list[list[float]], int]:
    """Vectors in input order, and the prompt tokens they cost. Provider requests are batched."""
    vectors: list[list[float]] = []
    tokens = 0
    dimensions = kwargs.get("dimensions")  # the only embedding argument EMBED_KWARGS allows
    for start in range(0, len(inputs), PROVIDER_BATCH):
        response = _openai().embeddings.create(model=model, input=inputs[start:start + PROVIDER_BATCH],
                                               dimensions=dimensions if isinstance(dimensions, int) else omit)
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
        tokens += response.usage.prompt_tokens
    return vectors, tokens
