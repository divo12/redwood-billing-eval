"""The verifier's side of the model gateway: OpenAI calls for the agent and the simulated developer.

`generate` turns conversation messages into one Responses API completion; `embed` returns vectors in
input order. The allowed models mirror environment/app/framework/deployment_manifest.json.
"""

from __future__ import annotations

import functools
import json
from typing import cast

from openai import OpenAI, omit
from openai.types.responses import Response
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming
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
MODELS = (ModelConfig("gpt-6-sol", {"reasoning_effort": "high"}),)
EMBEDDING_MODELS = (ModelConfig("text-embedding-3-small"), ModelConfig("text-embedding-3-large"))
ALLOWED_KWARGS = {"reasoning_effort", "max_output_tokens"}
EMBED_KWARGS = {"dimensions"}


def json_list(items: list[Json] | list[JsonObject] | list[float] | list[list[Json]]) -> list[Json]:
    return list(items)


@functools.cache
def _openai() -> OpenAI:
    """One shared client, created on first use."""
    return OpenAI(timeout=300.0, max_retries=3)


def _to_input(messages: list[Message]) -> tuple[list[Json], str | None]:
    """Conversation messages as Responses API items, plus the system prompt."""
    instructions = None
    items: list[Json] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            instructions = message.content
            continue
        items.extend(_input_items(message))
    return items, instructions


def _input_items(message: Message) -> list[JsonObject]:
    if isinstance(message, (UserMessage, AssistantMessage)):
        items: list[JsonObject] = [{"role": message.role, "content": message.content}] \
            if message.content else []
        return items + [_call_item(call) for call in message.tool_calls or []]
    if isinstance(message, ToolMessage):
        return [_tool_output(message)]
    if isinstance(message, MultiToolMessage):
        return [_tool_output(tool) for tool in message.tool_messages]
    return []


def _call_item(call: ToolCall) -> JsonObject:
    return {"type": "function_call", "name": call.name, "arguments": json.dumps(call.arguments),
            "call_id": call.id}


def _tool_output(message: ToolMessage) -> JsonObject:
    return {"type": "function_call_output", "call_id": message.id, "output": message.content or ""}


def generate(model: str, messages: list[Message], tools: list[JsonObject] | None,
             tool_choice: Json | None, **kwargs: Json) -> AssistantMessage:
    """One Responses API completion; reasoning-only answers are asked again up to three times."""
    request = _request_payload(model, messages, tools, tool_choice, kwargs)
    for _ in range(3):  # reasoning-only completions happen; ask again
        message = _parse_response(_openai().responses.create(**_create_params(request)))
        if message.content or message.tool_calls:
            return message
    raise RuntimeError(f"{model} returned an empty completion three times")


def _request_payload(model: str, messages: list[Message], tools: list[JsonObject] | None,
                     tool_choice: Json | None, kwargs: JsonObject) -> JsonObject:
    kwargs = dict(kwargs)
    effort = kwargs.pop("reasoning_effort", None)
    items, instructions = _to_input(messages)
    request: JsonObject = {"model": model, "input": items, "store": False, **kwargs}
    if instructions:
        request["instructions"] = instructions
    if tools:
        request["tools"] = json_list(tools)
        request["tool_choice"] = tool_choice or "auto"
    if effort:
        request["reasoning"] = {"effort": effort}
    return request


def _create_params(request: JsonObject) -> ResponseCreateParamsNonStreaming:
    """Wire JSON as the SDK request type; the provider validates it (the SDK TypedDicts are stricter than the API)."""
    return cast(ResponseCreateParamsNonStreaming, request)


def _output_text(response: Response) -> str | None:
    text = [part.text for item in response.output if item.type == "message"
            for part in item.content if part.type == "output_text" and part.text.strip()]
    return "\n".join(text) or None


def _function_calls(response: Response) -> list[ToolCall] | None:
    calls = [ToolCall(item.call_id, item.name, json.loads(item.arguments or "{}"))
             for item in response.output if item.type == "function_call"]
    return calls or None


def _usage(response: Response) -> dict[str, int]:
    usage = response.usage
    return {"prompt_tokens": usage.input_tokens if usage else 0,
            "completion_tokens": usage.output_tokens if usage else 0}


def _parse_response(response: Response) -> AssistantMessage:
    return AssistantMessage(content=_output_text(response), tool_calls=_function_calls(response),
                            usage=_usage(response))


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
