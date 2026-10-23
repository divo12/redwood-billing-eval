"""The model gateway shared by grading (tests/run re-exports it) and the practice service: LiteLLM for the agent's
turns and the developer's wording, a local model for embeddings.

    SUPPORT_MODEL               the deployed support model, a LiteLLM id (default openai/gpt-6-luna); the image
                                writes it into /app/framework/deployment_manifest.json: the one model an agent
                                may call
    SUPPORT_REASONING_EFFORT    reasoning effort for the support model (default high; empty: the provider default)
    USER_MODEL                  the model that words the scripted developer's lines (a LiteLLM id; unset: the
                                plain script)

Provider credentials are whatever LiteLLM reads for that id: OPENAI_API_KEY and OPENAI_BASE_URL for openai/
(an Azure `/openai/v1` endpoint works), ANTHROPIC_API_KEY for anthropic/, OPENROUTER_API_KEY for openrouter/.
`generate` turns conversation messages into one chat completion; `embed` returns vectors in input order.
"""

from __future__ import annotations

import json
import os
from typing import cast

import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    BudgetExceededError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from redwood_kit import conversation
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


class ProviderError(RuntimeError):
    """The model provider failed (credentials, billing, limits, outage): an invalid run, never a score."""


DEFAULT_MODEL = "openai/gpt-6-luna"
SUPPORT_MODEL = os.environ.get("SUPPORT_MODEL") or DEFAULT_MODEL
EFFORT = "SUPPORT_REASONING_EFFORT"
USER_MODEL = "USER_MODEL"
MODELS = (ModelConfig(SUPPORT_MODEL),)  # what the image wrote into deployment_manifest.json
EMBEDDING_MODELS = (ModelConfig("sentence-transformers/all-MiniLM-L6-v2"),)
ALLOWED_KWARGS = conversation.MODEL_KWARGS  # the agent contract: shared with the practice runner
EMBED_KWARGS = conversation.EMBED_KWARGS
# The conversation opens with the agent's greeting; some providers need a user turn first.
OPENING = "(The developer has opened a billing support chat.)"
# Anthropic prompt caching (repeated prefixes bill at ~0.1x); other providers cache on their own.
CACHE_POINTS: list[JsonObject] = [{"location": "message", "role": "system"}, {"location": "message", "index": -1}]
PROVIDER_FAILURES = (APIConnectionError, AuthenticationError, PermissionDeniedError, RateLimitError,
                     InternalServerError, ServiceUnavailableError, Timeout, BudgetExceededError)
ACCOUNT_PROBLEMS = ("credit balance", "insufficient_quota")  # billing problems some APIs report as a 400
litellm.suppress_debug_info = True
litellm.drop_params = True  # a provider without reasoning_effort ignores it instead of failing


def json_list(items: list[Json] | list[JsonObject] | list[float] | list[list[Json]]) -> list[Json]:
    return list(items)


def _assistant(message: AssistantMessage) -> list[Json]:
    calls: list[Json] = [{"id": call.id, "type": "function",
                          "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
                         for call in message.tool_calls or []]
    if not (message.content or calls):
        return []
    return [{"role": "assistant", "content": message.content, **({"tool_calls": calls} if calls else {})}]


def _chat(message: Message) -> list[Json]:
    """One message as chat-completion messages."""
    if isinstance(message, (SystemMessage, UserMessage)):
        return [{"role": message.role, "content": message.content}] if message.content else []
    if isinstance(message, AssistantMessage):
        return _assistant(message)
    tools = [message] if isinstance(message, ToolMessage) else (
        message.tool_messages if isinstance(message, MultiToolMessage) else [])
    return [{"role": "tool", "tool_call_id": tool.id, "content": tool.content or ""} for tool in tools]


def _messages(messages: list[Message]) -> list[Json]:
    chat = [item for message in messages for item in _chat(message)]
    first = next((m for m in chat if isinstance(m, dict) and m["role"] != "system"), None)
    if isinstance(first, dict) and first["role"] == "assistant":
        chat.insert(chat.index(first), {"role": "user", "content": OPENING})
    return chat


def _tool_choice(choice: Json | None) -> Json:
    """The runtime's tool_choice ("auto", "none", "required" or {"type": "function", "name": ...}) for chat."""
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return {"type": "function", "function": {"name": choice["name"]}}
    return choice if choice in ("none", "required") else "auto"


def _request(model: str, messages: list[Message], tools: list[JsonObject] | None, tool_choice: Json | None,
             kwargs: JsonObject) -> JsonObject:
    request: JsonObject = {"model": model, "messages": _messages(messages)}
    if isinstance(max_tokens := kwargs.get("max_output_tokens"), int):  # else the provider's default
        request["max_tokens"] = max_tokens
    effort = os.environ.get(EFFORT, "high")
    if effort:
        request["reasoning_effort"] = effort
    if "anthropic" in model:
        request["cache_control_injection_points"] = list[Json](CACHE_POINTS)
    if tools:
        request["tools"] = [{"type": "function", "function": {
            "name": tool["name"], "description": tool.get("description") or "",
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}} for tool in tools]
        request["tool_choice"] = _tool_choice(tool_choice)
    return request


def _send(request: JsonObject) -> litellm.ModelResponse:
    """One provider call; the provider's own failures become ProviderError, the agent's bad requests stay errors."""
    try:
        return cast(litellm.ModelResponse, litellm.completion(**request, timeout=300, num_retries=10))
    except PROVIDER_FAILURES as error:
        raise ProviderError(f"{type(error).__name__}: {error}") from error
    except BadRequestError as error:
        if any(text in str(error) for text in ACCOUNT_PROBLEMS):
            raise ProviderError(f"{type(error).__name__}: {error}") from error
        raise


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _usage(response: litellm.ModelResponse) -> dict[str, int]:
    usage = cast(JsonObject, response.model_dump().get("usage") or {})
    details = usage.get("prompt_tokens_details")
    read = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
    return {"prompt_tokens": _int(usage.get("prompt_tokens")),
            "completion_tokens": _int(usage.get("completion_tokens")),
            "cache_write_tokens": _int(usage.get("cache_creation_input_tokens")), "cache_read_tokens": read}


def _call(call: JsonObject) -> ToolCall:
    function = call.get("function")
    function = function if isinstance(function, dict) else {}
    arguments = function.get("arguments")
    return ToolCall(str(call.get("id")), str(function.get("name")),
                    json.loads(arguments) if isinstance(arguments, str) and arguments else {})


def _parse(response: litellm.ModelResponse) -> AssistantMessage:
    message = cast(JsonObject, response.model_dump()["choices"][0]["message"])
    text = message.get("content")
    raw_calls = message.get("tool_calls")
    calls = [_call(call) for call in raw_calls if isinstance(call, dict)] if isinstance(raw_calls, list) else []
    return AssistantMessage(content=text if isinstance(text, str) and text.strip() else None,
                            tool_calls=calls or None, usage=_usage(response))


def generate(model: str, messages: list[Message], tools: list[JsonObject] | None,
             tool_choice: Json | None, **kwargs: Json) -> AssistantMessage:
    """One chat completion; empty (reasoning-only) answers are asked again up to three times."""
    request = _request(model, messages, tools, tool_choice, dict(kwargs))
    for _ in range(3):
        message = _parse(_send(request))
        if message.content or message.tool_calls:
            return message
    raise RuntimeError(f"{model} returned an empty completion three times")


def complete(model: str, instructions: str, text: str, effort: str = "low") -> str | None:
    """One plain-text answer, for helpers outside the agent (the developer's wording)."""
    request: JsonObject = {"model": model, "reasoning_effort": effort,
                           "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}]}
    return _parse(_send(request)).content


def embed(model: str, inputs: list[str], **kwargs: Json) -> tuple[list[list[float]], int]:
    """Vectors in input order from the local model, and the (zero) provider tokens they cost."""
    del kwargs  # EMBED_KWARGS is empty: run_agent refuses any argument before this is called
    return encode(model, inputs), 0
