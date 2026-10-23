"""The active workspace context and typed gateways supplied while building an agent."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .jsontypes import Json, JsonObject
from .messages import AssistantMessage, Message, from_dict, to_dict
from .toolkit import Tool

if TYPE_CHECKING:
    from .billing import Billing


@dataclass(frozen=True)
class ActionInterface:
    available: tuple[Tool, ...]

    def select(self, names: Sequence[str]) -> tuple[Tool, ...]:
        by_name = {tool.name: tool for tool in self.available}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise ValueError(f"Unknown actions: {missing}")
        return tuple(by_name[name] for name in names)


@dataclass(frozen=True)
class KitResources:
    root: Path
    files: tuple[str, ...]

    @classmethod
    def from_root(cls, root: Path) -> KitResources:
        root = root.resolve()
        files = tuple(
            path.relative_to(root).as_posix()
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        )
        return cls(root, files)

    def path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"Path escapes the kit root: {relative_path!r}")
        return candidate

    def read_text(self, relative_path: str) -> str:
        return self.path(relative_path).read_text()


@dataclass(frozen=True)
class ModelConfig:
    model: str
    constraints: Mapping[str, Json] = field(default_factory=dict)


def _single_config(models: Sequence[ModelConfig], model: str) -> ModelConfig:
    matches = [config for config in models if config.model == model]
    if not matches:
        allowed = ", ".join(repr(config.model) for config in models)
        raise ValueError(f"Model {model!r} is not allowed; choose one of {allowed}")
    if len(matches) > 1:
        raise ValueError(f"Model {model!r} has several configurations; not supported")
    return matches[0]


def _constraint_choices(expected: Json) -> list[Json] | None:
    if not isinstance(expected, dict):
        return None
    choices = expected.get("one_of")
    return choices if isinstance(choices, list) else None


def _resolve_constraint(name: str, expected: Json, kwargs: Mapping[str, Json], resolved: JsonObject) -> None:
    choices = _constraint_choices(expected)
    if choices is not None:
        if kwargs.get(name) not in choices:
            raise ValueError(f"{name} must be chosen from {choices!r}")
        return
    if name in kwargs and kwargs[name] != expected:
        raise ValueError(f"{name} must be {expected!r}, got {kwargs[name]!r}")
    resolved[name] = expected


def resolve_call(
    models: Sequence[ModelConfig], model: str, kwargs: Mapping[str, Json]
) -> JsonObject:
    """Apply pinned constraints, require explicit choices, reject conflicts."""
    config = _single_config(models, model)
    request_args: JsonObject = {}
    for name, expected in config.constraints.items():
        _resolve_constraint(name, expected, kwargs, request_args)
    request_args.update(kwargs)
    return request_args


@dataclass
class ModelGateway:
    models: tuple[ModelConfig, ...]
    _send: Callable[[JsonObject], JsonObject]
    embedding_models: tuple[ModelConfig, ...] = ()

    def embed(self, *, model: str, inputs: Sequence[str], **kwargs: Json) -> list[list[float]]:
        """One vector per input, in order, from an allowed embedding model."""
        reply = self._send(
            {
                "call": "embed",
                "model": model,
                "inputs": list(inputs),
                "kwargs": resolve_call(self.embedding_models, model, kwargs),
            }
        )
        vectors = reply.get("vectors")
        if not isinstance(vectors, list):
            raise RuntimeError("embedding gateway returned invalid vectors")
        parsed: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list):
                raise RuntimeError("embedding gateway returned invalid vectors")
            parsed_vector: list[float] = []
            for value in vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise RuntimeError("embedding gateway returned invalid vectors")
                parsed_vector.append(float(value))
            parsed.append(parsed_vector)
        return parsed

    def generate(
        self,
        *,
        model: str,
        messages: list[Message],
        actions: Sequence[Tool] | None = None,
        tool_choice: Json = None,
        **kwargs: Json,
    ) -> AssistantMessage:
        reply = self._send(
            {
                "call": "model",
                "model": model,
                "messages": [to_dict(message) for message in messages],
                "tools": [tool.openai_schema for tool in actions] if actions else None,
                "tool_choice": tool_choice,
                "kwargs": resolve_call(self.models, model, kwargs),
            }
        )
        raw_message = reply.get("message")
        if not isinstance(raw_message, dict):
            raise RuntimeError("model gateway returned an invalid message")
        message = from_dict(raw_message)
        if not isinstance(message, AssistantMessage):
            raise RuntimeError("model gateway returned a non-assistant message")
        if message.is_tool_call() and message.has_text_content():
            # Models often narrate before calling a tool. A turn is either text to the
            # customer or tool calls, never both; the tool calls carry the work.
            message = replace(message, content=None)
        return message


@dataclass(frozen=True)
class AgentBuildContext:
    action_interface: ActionInterface
    resources: KitResources
    model_gateway: ModelGateway
    runtime_config: Mapping[str, str]
    billing: Billing | None = None


_active: ContextVar[AgentBuildContext | None] = ContextVar("agent_context", default=None)


@contextmanager
def activate_agent_context(context: AgentBuildContext) -> Iterator[None]:
    token = _active.set(context)
    try:
        yield
    finally:
        _active.reset(token)


def get_agent_context() -> AgentBuildContext:
    context = _active.get()
    if context is None:
        raise RuntimeError("Agent context is only available while create_agent() is executing")
    return context
