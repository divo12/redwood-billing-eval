"""Tool declarations connect workspace methods to the runtime's JSON tool interface."""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, get_type_hints

from pydantic import create_model

from .jsontypes import Json, JsonObject, is_json_object

if TYPE_CHECKING:
    from .agent_context import ModelGateway
    from .billing import Billing
    from .hooks import ToolRunner


class ToolType(StrEnum):
    READ = "read"
    WRITE = "write"
    THINK = "think"
    GENERIC = "generic"


_TOOL_TYPES: dict[object, ToolType] = {}


def is_tool[**P](tool_type: ToolType = ToolType.READ) -> Callable[[Callable[P, object]], Callable[P, object]]:
    def decorate(function: Callable[P, object]) -> Callable[P, object]:
        _TOOL_TYPES[function] = tool_type
        return function

    return decorate


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: JsonObject

    @property
    def openai_schema(self) -> JsonObject:
        return {"type": "function", "name": self.name, "description": self.description, "parameters": self.parameters}


def _docstring_parts(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (description, {arg: description})."""
    head, _, tail = inspect.cleandoc(doc or "").partition("Args:")
    tail = re.split(r"\n\s*(?:Returns|Raises|Examples?):", tail)[0]
    args: dict[str, str] = {}
    current: str | None = None
    for line in tail.splitlines():
        match = re.match(r"\s*(\w+)(?:\s*\([^)]*\))?:\s*(.*)", line)
        if match:
            current = match.group(1)
            args[current] = match.group(2).strip()
        elif current is not None and line.strip():
            args[current] += " " + line.strip()
    return " ".join(head.split()), args


def tool_from_method[**P](method: Callable[P, object]) -> Tool:
    description, arg_docs = _docstring_parts(method.__doc__)
    hints: dict[str, object] = get_type_hints(method)
    fields: dict[str, tuple[object, object]] = {}
    for name, parameter in inspect.signature(method).parameters.items():
        if name == "self":
            continue
        annotation = hints.get(name, object)
        default: object = ...
        if parameter.default is not inspect.Parameter.empty:
            default = parameter.default
        fields[name] = (annotation, default)
    schema_value: object = create_model(
        method.__name__,
        __config__=None,
        __doc__=None,
        __base__=None,
        __module__=__name__,
        __validators__=None,
        __cls_kwargs__=None,
        __qualname__=None,
        **fields,
    ).model_json_schema()
    if not is_json_object(schema_value):
        raise TypeError(f"tool schema for {method.__name__} must be a JSON object")
    schema = schema_value
    for name, text in arg_docs.items():
        properties = schema.get("properties")
        if not is_json_object(properties):
            continue
        parameter_schema = properties.get(name)
        if is_json_object(parameter_schema):
            parameter_schema["description"] = text
    schema.pop("title", None)
    return Tool(method.__name__, description, schema)


class ToolKitBase:
    """Anything with `@is_tool` methods; `get_tools()` lists them, `call()` runs one.

    The runtime sets `gateway` (ModelGateway), `runner`, and `billing` before any tool runs.
    """

    gateway: ModelGateway
    runner: ToolRunner
    billing: Billing | None = None

    def get_tools(self) -> list[Tool]:
        tools = []
        for _, member in inspect.getmembers(self, inspect.ismethod):
            if member.__func__ in _TOOL_TYPES:
                tools.append(tool_from_method(member))
        return tools

    def call(self, name: str, /, **arguments: Json) -> object:
        for _, method in inspect.getmembers(self, inspect.ismethod):
            if method.__name__ == name and method.__func__ in _TOOL_TYPES:
                return method(**arguments)
        raise ValueError(f"Tool {name} not found")
