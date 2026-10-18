"""Typed conversation messages and their JSON wire encoders and decoders."""
from __future__ import annotations

from dataclasses import dataclass, field

from .jsontypes import Json, JsonObject, is_json_object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: JsonObject = field(default_factory=dict)


@dataclass
class SystemMessage:
    content: str
    role: str = "system"


class _Turn:
    content: str | None
    tool_calls: list[ToolCall] | None

    def is_tool_call(self) -> bool:
        return bool(self.tool_calls)

    def has_text_content(self) -> bool:
        return bool(self.content and self.content.strip())


@dataclass
class UserMessage(_Turn):
    content: str | None = None
    role: str = "user"
    tool_calls: list[ToolCall] | None = None


@dataclass
class AssistantMessage(_Turn):
    content: str | None = None
    role: str = "assistant"
    tool_calls: list[ToolCall] | None = None
    usage: dict[str, int] | None = None


@dataclass
class ToolMessage:
    id: str
    content: str | None = None
    role: str = "tool"
    error: bool = False


@dataclass
class MultiToolMessage:
    tool_messages: list[ToolMessage]
    role: str = "tool"


Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage | MultiToolMessage


def _tool_call_json(call: ToolCall) -> JsonObject:
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _tool_message_json(message: ToolMessage) -> JsonObject:
    return {
        "id": message.id,
        "content": message.content,
        "role": message.role,
        "error": message.error,
    }


def to_dict(message: Message) -> JsonObject:
    if isinstance(message, SystemMessage):
        return {"content": message.content, "role": message.role}
    if isinstance(message, ToolMessage):
        return _tool_message_json(message)
    if isinstance(message, MultiToolMessage):
        return {"tool_messages": [_tool_message_json(item) for item in message.tool_messages], "role": message.role}
    value: JsonObject = {
        "content": message.content,
        "role": message.role,
        "tool_calls": None if message.tool_calls is None else [_tool_call_json(call) for call in message.tool_calls],
    }
    if isinstance(message, AssistantMessage):
        usage: Json = None
        if message.usage is not None:
            usage_data: JsonObject = {name: count for name, count in message.usage.items()}
            usage = usage_data
        value["usage"] = usage
    return value


def _string(data: JsonObject, name: str) -> str:
    value = data[name]
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _optional_string(data: JsonObject, name: str) -> str | None:
    value = data.get(name)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null")
    return value


def _arguments(data: JsonObject) -> JsonObject:
    value = data.get("arguments", {})
    if not is_json_object(value):
        raise TypeError("arguments must be an object")
    return value


def _reject_extra(data: JsonObject, accepted: tuple[str, ...], name: str) -> None:
    for key in data:
        if key not in accepted:
            raise TypeError(f"{name}.__init__() got an unexpected keyword argument '{key}'")


def _tool_call(data: JsonObject) -> ToolCall:
    _reject_extra(data, ("id", "name", "arguments"), "ToolCall")
    return ToolCall(_string(data, "id"), _string(data, "name"), _arguments(data))


def _tool_calls(data: JsonObject) -> list[ToolCall] | None:
    value = data.get("tool_calls")
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError("tool_calls must be a list")
    calls: list[ToolCall] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("tool_calls must contain objects")
        calls.append(_tool_call(item))
    return calls


def _usage(data: JsonObject) -> dict[str, int] | None:
    value = data.get("usage")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("usage must be an object of integer counts")
    usage: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or type(count) is not int:
            raise TypeError("usage must be an object of integer counts")
        usage[key] = count
    return usage


def _role(data: JsonObject, default: str) -> str:
    return _string(data, "role") if "role" in data else default


def _system(data: JsonObject) -> Message:
    _reject_extra(data, ("content", "role"), "SystemMessage")
    return SystemMessage(_string(data, "content"), _role(data, "system"))


def _user(data: JsonObject) -> Message:
    _reject_extra(data, ("content", "role", "tool_calls"), "UserMessage")
    return UserMessage(
        _optional_string(data, "content"),
        _role(data, "user"),
        _tool_calls(data),
    )


def _assistant(data: JsonObject) -> Message:
    _reject_extra(data, ("content", "role", "tool_calls", "usage"), "AssistantMessage")
    return AssistantMessage(
        _optional_string(data, "content"),
        _role(data, "assistant"),
        _tool_calls(data),
        _usage(data),
    )


def _tool_message(data: JsonObject) -> ToolMessage:
    _reject_extra(data, ("id", "content", "role", "error"), "ToolMessage")
    error = data.get("error", False)
    if not isinstance(error, bool):
        raise TypeError("error must be a boolean")
    return ToolMessage(_string(data, "id"), _optional_string(data, "content"), _role(data, "tool"), error)


def _tool(data: JsonObject) -> Message:
    return _tool_message(data)


def _multi_tool(data: JsonObject) -> Message:
    _reject_extra(data, ("tool_messages", "role"), "MultiToolMessage")
    values = data["tool_messages"]
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise TypeError("tool_messages must be a list of objects")
    tool_messages: list[ToolMessage] = []
    for item in values:
        if not isinstance(item, dict):
            raise TypeError("tool_messages must be a list of objects")
        tool_messages.append(_tool_message(item))
    return MultiToolMessage(tool_messages)


_PARSERS = {"system": _system, "user": _user, "assistant": _assistant, "tool": _tool}


def from_dict(data: JsonObject) -> Message:
    if "tool_messages" in data:
        return _multi_tool(data)
    role = data.get("role")
    parser = _PARSERS.get(role, _user) if isinstance(role, str) else _user
    return parser(data)
