"""Run workspace agents in a sandbox and exchange JSON messages with the host."""
from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Protocol, TextIO, TypeGuard, runtime_checkable

from .agent_context import (
    ActionInterface,
    AgentBuildContext,
    KitResources,
    ModelConfig,
    ModelGateway,
    activate_agent_context,
)
from .billing import Billing
from .hooks import HookKitBase, ToolRunner
from .jsontypes import Json, JsonObject, is_json_object
from .messages import Message, ToolCall, UserMessage, from_dict, to_dict
from .toolkit import ToolKitBase

RUNTIME_CONFIG = {"company": "Redwood Inference", "role": "billing_support"}
_protocol_out = sys.stdout
_protocol_in = sys.stdin


class HostOp(StrEnum):
    INIT = "init"
    TURN = "turn"
    TOOLS = "tools"


class HostCall(StrEnum):
    MODEL = "model"
    EMBED = "embed"
    BILLING = "billing"


@runtime_checkable
class SupportAgent(Protocol):
    def get_init_state(self, *, message_history: list[Message]) -> object: ...

    def generate_next_message(self, message: Message, state: object) -> tuple[Message, object]: ...


@runtime_checkable
class StopsConversation(Protocol):
    def is_stop(self, message: Message) -> bool: ...


class WorkspaceFactory(Protocol):
    def __call__(self) -> object: ...


def _is_factory(value: object) -> TypeGuard[WorkspaceFactory]:
    return callable(value)


def _write(payload: JsonObject) -> None:
    _protocol_out.write(json.dumps(payload) + "\n")
    _protocol_out.flush()


def _read() -> JsonObject:
    line = _protocol_in.readline()
    if not line:
        raise SystemExit(0)
    value = json.loads(line)
    if not is_json_object(value):
        raise TypeError("wire message must be an object")
    return value


def _ask(payload: JsonObject) -> JsonObject:
    _write(payload)
    reply = _read()
    if not reply.get("ok"):
        error = reply.get("error", "host rejected the request")
        raise RuntimeError(str(error))
    return reply


def _factory(module: ModuleType, name: str) -> WorkspaceFactory:
    candidate = module.__dict__.get(name)
    if not _is_factory(candidate):
        raise TypeError(f"workspace module has no callable {name}")
    return candidate


def _load_toolkit() -> ToolKitBase:
    instance = _factory(importlib.import_module("tools"), "Tools")()
    if not isinstance(instance, ToolKitBase):
        raise TypeError("workspace Tools must inherit ToolKitBase")
    return instance


def _load_hooks(root: Path) -> HookKitBase | None:
    if not (root / "hooks.py").is_file():
        return None
    instance = _factory(importlib.import_module("hooks"), "Hooks")()
    if not isinstance(instance, HookKitBase):
        raise TypeError("workspace Hooks must inherit HookKitBase")
    return instance


def _object(value: Json, name: str) -> JsonObject:
    if not is_json_object(value):
        raise TypeError(f"{name} must be an object")
    return value


def _model_config(value: Json) -> ModelConfig:
    data = _object(value, "model configuration")
    model = data.get("model")
    constraints = data.get("constraints", {})
    if not isinstance(model, str) or not is_json_object(constraints):
        raise TypeError("model configuration is invalid")
    return ModelConfig(model, constraints)


def _model_configs(value: Json) -> tuple[ModelConfig, ...]:
    if not isinstance(value, list):
        raise TypeError("model configurations must be a list")
    return tuple(_model_config(item) for item in value)


def _init_gateway(init: JsonObject) -> ModelGateway:
    models = _model_configs(init["models"])
    embeddings = _model_configs(init.get("embedding_models", []))
    return ModelGateway(models, _ask, embeddings)


def _build_agent(root: Path, toolkit: ToolKitBase, gateway: ModelGateway, runner: ToolRunner) -> SupportAgent:
    billing = Billing(_ask)
    toolkit.gateway = gateway
    toolkit.runner = runner
    toolkit.billing = billing
    context = AgentBuildContext(
        action_interface=ActionInterface(tuple(toolkit.get_tools())),
        resources=KitResources.from_root(root),
        model_gateway=gateway,
        runtime_config=RUNTIME_CONFIG,
        billing=billing,
    )
    with activate_agent_context(context):
        agent = _factory(importlib.import_module("agent"), "create_agent")()
    if not isinstance(agent, SupportAgent):
        raise TypeError("workspace agent does not implement the support-agent protocol")
    return agent


def _initial_state(agent: SupportAgent, init: JsonObject) -> object:
    message = from_dict(_object(init["first_message"], "first_message"))
    return agent.get_init_state(message_history=[message])


def _stop(agent: SupportAgent, message: Message) -> bool:
    if isinstance(agent, StopsConversation):
        return bool(agent.is_stop(message))
    return False


def _turn(request: JsonObject, agent: SupportAgent, runner: ToolRunner, state: object) -> tuple[JsonObject, object]:
    message = from_dict(_object(request["message"], "message"))
    if isinstance(message, UserMessage):
        message.content = runner.user_message(message.content or "")
    response, new_state = agent.generate_next_message(message, state)
    return {"ok": True, "message": to_dict(response), "stop": _stop(agent, response)}, new_state


def _tool_calls(value: Json) -> list[ToolCall]:
    if not isinstance(value, list):
        raise TypeError("tool_calls must be a list")
    calls: list[ToolCall] = []
    for item in value:
        data = _object(item, "tool call")
        call_id = data.get("id")
        name = data.get("name")
        arguments = data.get("arguments", {})
        if not isinstance(call_id, str) or not isinstance(name, str) or not is_json_object(arguments):
            raise TypeError("tool call is invalid")
        calls.append(ToolCall(call_id, name, arguments))
    return calls


def _tools(request: JsonObject, runner: ToolRunner) -> JsonObject:
    calls = _tool_calls(request["tool_calls"])
    return {"ok": True, "tool_messages": [to_dict(runner.run(call)) for call in calls]}


def _handle_request(
    request: JsonObject,
    agent: SupportAgent,
    runner: ToolRunner,
    state: object,
) -> tuple[JsonObject | None, object]:
    operation = request.get("op")
    if operation == HostOp.TURN:
        response, new_state = _turn(request, agent, runner, state)
        return response, new_state
    if operation == HostOp.TOOLS:
        return _tools(request, runner), state
    return None, state


def _serve(agent: SupportAgent, runner: ToolRunner, state: object) -> None:
    while True:
        request = _read()
        try:
            response, state = _handle_request(request, agent, runner, state)
            if response is None:
                return
            _write(response)
        except Exception:
            _write({"ok": False, "error": traceback.format_exc(limit=8)})


def _redirect_stdio(null_input: TextIO) -> None:
    global _protocol_out, _protocol_in
    _protocol_out, _protocol_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = sys.stderr, null_input


def _run_candidate() -> None:
    init = _read()
    root = Path.cwd()
    sys.path.insert(0, str(root))
    toolkit = _load_toolkit()
    runner = ToolRunner(toolkit, _load_hooks(root))
    gateway = _init_gateway(init)
    agent = _build_agent(root, toolkit, gateway, runner)
    state = _initial_state(agent, init)
    _write({"ok": True})
    _serve(agent, runner, state)


def main() -> None:
    with open(os.devnull) as null_input:
        _redirect_stdio(null_input)
        _run_candidate()


if __name__ == "__main__":
    main()
