"""Hook declarations connect workspace callbacks to message and tool execution.

The runtime, not the agent, fires them, so they hold for every tool call,
including those a subagent makes.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from types import MethodType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .jsontypes import JsonObject, is_json_object
from .messages import ToolCall, ToolMessage

if TYPE_CHECKING:
    from .toolkit import ToolKitBase


class HookEvent(StrEnum):
    USER_MESSAGE = "user_message"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


EVENTS = tuple(event.value for event in HookEvent)


@dataclass(frozen=True)
class HookBinding:
    event: HookEvent
    match: str


_HOOKS: dict[object, HookBinding] = {}


@runtime_checkable
class _UserMessageHook(Protocol):
    def __call__(self, text: str) -> object: ...


@runtime_checkable
class _PreToolHook(Protocol):
    def __call__(self, tool: str, arguments: JsonObject) -> object: ...


@runtime_checkable
class _PostToolHook(Protocol):
    def __call__(self, tool: str, arguments: JsonObject, result: object) -> object: ...


class Blocked(Exception):
    """Raise from a pre_tool hook to refuse the call; the message goes back to the model."""


def hook[F](event: str, match: str = "*") -> Callable[[F], F]:
    """Run the method on `event`; tool events only for tool names matching the `match` glob.

    user_message(self, text) -> str | None   replacement text, or None to keep it
    pre_tool(self, tool, args) -> dict | None  replacement arguments; raise Blocked to refuse
    post_tool(self, tool, args, result) -> object | None  replacement result
    """
    if event not in EVENTS:
        raise ValueError(f"unknown hook event {event!r}; use one of {EVENTS}")

    def decorate(function: F) -> F:
        _HOOKS[function] = HookBinding(HookEvent(event), match)
        return function

    return decorate


class HookKitBase:
    """Anything with `@hook` methods. Hooks for one event run in method-name order."""

    def hooks(self, event: str, tool: str = "") -> list[MethodType]:
        handlers: list[MethodType] = []
        for _, member in inspect.getmembers(self, inspect.ismethod):
            binding = _HOOKS.get(member.__func__)
            if binding is not None and binding.event == event and fnmatch(tool, binding.match):
                handlers.append(member)
        return handlers


class ToolRunner:
    """Runs tool calls through pre_tool and post_tool hooks. Every tool call goes through here."""

    def __init__(self, toolkit: ToolKitBase, hooks: HookKitBase | None = None) -> None:
        self.toolkit = toolkit
        self._hooks = hooks or HookKitBase()

    def user_message(self, text: str) -> str:
        for handler in self._hooks.hooks(HookEvent.USER_MESSAGE):
            if not isinstance(handler, _UserMessageHook):
                raise TypeError(f"{handler.__name__} is not a user_message hook")
            if (replaced := handler(text)) is not None:
                if not isinstance(replaced, str):
                    raise TypeError(f"{handler.__name__} must return a string or None")
                text = replaced
        return text

    def run(self, call: ToolCall) -> ToolMessage:
        try:  # a failing tool or hook is data for the agent, not a crash
            args = dict(call.arguments)
            for handler in self._hooks.hooks(HookEvent.PRE_TOOL, call.name):
                if not isinstance(handler, _PreToolHook):
                    raise TypeError(f"{handler.__name__} is not a pre_tool hook")
                if (replaced := handler(call.name, args)) is not None:
                    if not is_json_object(replaced):
                        raise TypeError(f"{handler.__name__} must return an object or None")
                    args = replaced
            result = self.toolkit.call(call.name, **args)
            for handler in self._hooks.hooks(HookEvent.POST_TOOL, call.name):
                if not isinstance(handler, _PostToolHook):
                    raise TypeError(f"{handler.__name__} is not a post_tool hook")
                if (replaced := handler(call.name, args, result)) is not None:
                    result = replaced
            return ToolMessage(call.id, json.dumps(result, default=str))
        except Blocked as error:
            return ToolMessage(call.id, f"Blocked: {error}", error=True)
        except Exception as error:
            return ToolMessage(call.id, f"Error: {error}", error=True)
