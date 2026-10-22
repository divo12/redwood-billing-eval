"""Testing only: serve the agent's model calls with an OpenAI Responses API model.

`gateway.generate` routes here when REDWOOD_SUPPORT_OVERRIDE is `openai:<model>`, so the
verifier can be exercised cheaply while the builder-visible deployment (manifest, starter
agent, oracle) keeps its Claude model. Official runs leave the override unset.
The client reads OPENAI_API_KEY and OPENAI_BASE_URL (e.g. an Azure `/openai/v1` endpoint).
"""
from __future__ import annotations

import functools
import json
import os
from typing import cast

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types.responses import Response
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming
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
from run.provider import ProviderError

PROVIDER_FAILURES = (APIConnectionError, AuthenticationError, PermissionDeniedError, RateLimitError,
                     InternalServerError)


EFFORT = "REDWOOD_OPENAI_REASONING_EFFORT"


@functools.cache
def _openai() -> OpenAI:
    # Harbor passes OPENAI_BASE_URL even when unset; an empty value must mean the SDK default.
    return OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, timeout=300.0, max_retries=3)


def _input_items(message: Message) -> list[Json]:
    if isinstance(message, (UserMessage, AssistantMessage)):
        items: list[Json] = [{"role": message.role, "content": message.content}] if message.content else []
        return items + [{"type": "function_call", "name": call.name, "arguments": json.dumps(call.arguments),
                         "call_id": call.id} for call in message.tool_calls or []]
    tools = [message] if isinstance(message, ToolMessage) else (
        message.tool_messages if isinstance(message, MultiToolMessage) else [])
    return [{"type": "function_call_output", "call_id": tool.id, "output": tool.content or ""} for tool in tools]


def _request(model: str, messages: list[Message], tools: list[JsonObject] | None, tool_choice: Json | None,
             kwargs: JsonObject) -> JsonObject:
    effort = os.environ.get(EFFORT)  # e.g. "high"; an explicit `reasoning` from the agent wins
    request: JsonObject = {"model": model, "store": False, **({"reasoning": {"effort": effort}} if effort else {}),
                           **kwargs, "input": [item for m in messages for item in _input_items(m)]}
    instructions = next((m.content for m in messages if isinstance(m, SystemMessage)), None)
    if instructions:
        request["instructions"] = instructions
    if tools:
        request["tools"] = list[Json](tools)
        request["tool_choice"] = tool_choice or "auto"
    return request


def _usage(response: Response) -> dict[str, int]:
    usage = response.usage
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}
    return {"prompt_tokens": usage.input_tokens, "completion_tokens": usage.output_tokens,
            "cache_write_tokens": 0, "cache_read_tokens": usage.input_tokens_details.cached_tokens}


def _text(response: Response) -> str | None:
    parts = (part for item in response.output if item.type == "message" for part in item.content)
    return "\n".join(p.text for p in parts if p.type == "output_text" and p.text.strip()) or None


def _calls(response: Response) -> list[ToolCall] | None:
    calls = [ToolCall(item.call_id, item.name, json.loads(item.arguments or "{}"))
             for item in response.output if item.type == "function_call"]
    return calls or None


def _parse(response: Response) -> AssistantMessage:
    return AssistantMessage(content=_text(response), tool_calls=_calls(response), usage=_usage(response))


def _send(request: JsonObject) -> Response:
    try:
        return _openai().responses.create(**cast(ResponseCreateParamsNonStreaming, request))
    except (APIStatusError, APIConnectionError) as error:
        if isinstance(error, PROVIDER_FAILURES):
            raise ProviderError(f"{type(error).__name__}: {error}") from error
        raise


def generate(model: str, messages: list[Message], tools: list[JsonObject] | None,
             tool_choice: Json | None, kwargs: JsonObject) -> AssistantMessage:
    """One Responses API completion; reasoning-only answers are asked again up to three times."""
    request = _request(model, messages, tools, tool_choice, kwargs)
    for _ in range(3):
        message = _parse(_send(request))
        if message.content or message.tool_calls:
            return message
    raise RuntimeError(f"{model} returned an empty completion three times")
