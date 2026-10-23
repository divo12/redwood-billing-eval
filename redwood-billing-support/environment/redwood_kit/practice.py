"""Practice conversations: run your workspace through the same conversation loop graded conversations use.

    python -m redwood_kit.practice SCRIPTS.json [--case ID ...] [--workspace /app/workspace] [--out DIR]

Each script plays one developer (format below). The runner freezes a read-only copy of the workspace,
starts the agent from it with the graded resource limits, and plays the conversation with
`redwood_kit.conversation`, the loop grading uses. The agent's calls are served like this:

    model      the deployed support model, through the practice model service (a per-release call budget)
    embed      the manifest's local embedding model, as at grading
    billing    a fresh practice console over the practice records (`development_billing()`); writes are discarded
    developer  your script's facts and consent answer (`redwood_kit.customer.Customer`)

The developer's opening and agenda lines are worded by the same model and rules graded conversations
use; a wording that drops a number or identifier falls back to your line as written. The developer
answers questions only through `ask_developer`, never in chat, and ends the conversation with a
transfer after a handoff or a stop when the agenda is used up.

Script file: {"cases": [{"id": "...", "persona": "how this developer writes",
                         "customer": {"opening": "...", "agenda": ["..."], "facts": {"org_id": "..."},
                                      "confirm": "yes", "confirm_reply": ""}}]}
`facts` keys come from redwood_kit.developer.FIELDS. Example scripts over the practice records:
/app/framework/client_api/practice_conversations.json.

Each case's transcript, console calls and developer calls are written to DIR/<id>.json (default
/tmp/redwood-practice) and summarized on stdout. A failure of the practice service itself (provider
outage, budget used up) stops the run with an error; it says nothing about your agent.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .agent_context import ModelConfig, resolve_call
from .billing.client import PRACTICE_SOCKET, reply_body
from .conversation import (
    EMBED_KWARGS,
    MODEL_KWARGS,
    AgentProcess,
    EmbedRequest,
    ModelRequest,
    Sandbox,
    Session,
    freeze,
    unlock,
    wire_payload,
)
from .customer import CustomerScript, WrittenCustomer
from .embeddings import checked_inputs, embedding_models, encode
from .jsontypes import Json, JsonObject
from .messages import Message, MultiToolMessage, ToolMessage, to_dict

KIT_PATH = "/opt"
LIMITED = True  # the graded resource limits (Linux; macOS cannot cap a process's address space)
MODEL_SOCKET = "/run/redwood-model/practice-model.sock"
MANIFEST = Path("/app/framework/deployment_manifest.json")
RELEASE_FILE = Path("/etc/redwood-release")
OUT = Path("/tmp/redwood-practice")
WORKSPACE = Path("/app/workspace")


class PracticeServiceError(RuntimeError):
    """The practice service failed (provider, budget, connection): the run is invalid, not the agent's doing."""


# The practice conversations' date and time, per release (UTC): the agent's clock reads this, as at grading.
PRACTICE_CLOCK = {"R0": "2026-09-15 12:00:00", "R1": "2027-04-08 12:00:00"}


class PracticeCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    persona: str = ""
    now: str = ""  # "YYYY-MM-DD HH:MM" (UTC) the conversation happens at; empty: the release's PRACTICE_CLOCK
    customer: CustomerScript


class PracticeFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    cases: list[PracticeCase]


def load_cases(path: Path, only: list[str] | None = None) -> list[PracticeCase]:
    cases = PracticeFile.model_validate_json(path.read_text()).cases
    if only:
        missing = sorted(set(only) - {case.id for case in cases})
        if missing:
            raise SystemExit(f"no case with id {', '.join(missing)} in {path}")
        cases = [case for case in cases if case.id in only]
    return cases


def agent_models(manifest: Path | None = None) -> tuple[ModelConfig, ...]:
    data = json.loads((manifest or MANIFEST).read_text())
    return tuple(ModelConfig(m["model"], m.get("constraints", {})) for m in data["allowed_agent_models"])


class _Line:
    """One JSON-lines connection to a Unix socket."""

    def __init__(self, path: str, name: str) -> None:
        self.name = name
        try:
            self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.connection.connect(path)
        except OSError as error:
            raise PracticeServiceError(f"cannot reach the {name} at {path}: {error}") from None
        self.stream = self.connection.makefile("rw", encoding="utf-8")

    def send(self, request: JsonObject) -> object:
        self.stream.write(json.dumps(request) + "\n")
        self.stream.flush()
        line = self.stream.readline()
        if not line:
            raise PracticeServiceError(f"the {self.name} closed the connection")
        return json.loads(line)

    def close(self) -> None:
        self.stream.close()
        self.connection.close()


class ModelService:
    """The practice model service: agent model calls and the developer's wording."""

    def __init__(self, release: str, path: str = MODEL_SOCKET) -> None:
        self.release = release
        self.line = _Line(path, "practice model service")
        self.conversation = ""
        self.conversation = str(self.call("open").get("conversation"))  # one of the release's practice conversations

    def call(self, op: str, **fields: Json) -> JsonObject:
        session: JsonObject = {"conversation": self.conversation} if self.conversation else {}
        reply = self.line.send({"op": op, "release": self.release, **session, **fields})
        if not isinstance(reply, dict):
            raise PracticeServiceError("the practice model service sent a malformed reply")
        if reply.get("ok") is True:
            return reply
        error = str(reply.get("error", "practice model service error"))
        if reply.get("kind") == "request":
            raise ValueError(error)  # the agent's own bad request: the agent sees it, as at grading
        raise PracticeServiceError(error)

    def close(self) -> None:
        self.line.close()


class PracticeCustomer(WrittenCustomer):
    """The script, worded by the practice model service with the grading rules."""

    def __init__(self, case: PracticeCase, service: ModelService) -> None:
        super().__init__(case.customer, case.persona)
        self.service = service

    def _model_text(self, line: str, instructions: str, conversation: str) -> str | None:
        # The service builds the instructions from the same rules; a failed wording falls back to the line,
        # as at grading, and shows as "fallback" in the developer calls.
        del instructions
        text = self.service.call("say", persona=self.persona, line=line, approval=self._approval(),
                                 transcript=conversation).get("text")
        return text if isinstance(text, str) else None


class PracticeSession(Session):
    """One practice conversation, served by the practice services."""

    passthrough = (PracticeServiceError,)

    def __init__(self, case: PracticeCase, frozen: Path, logs: Path, release: str,
                 services: tuple[ModelService, _Line] | None = None) -> None:
        """`services` (model service, console connection) defaults to the sockets of the deployment."""
        self.service, self.console = services or (ModelService(release), _Line(PRACTICE_SOCKET, "practice console"))
        super().__init__(PracticeCustomer(case, self.service))
        self.case, self.frozen, self.logs, self.release = case, frozen, logs, release
        self.home = Path(tempfile.mkdtemp(prefix="redwood-practice-home-"))  # the agent's private scratch
        self.agent_models, self.embedding_models = agent_models(), embedding_models(MANIFEST)
        self.console_calls: list[JsonObject] = []
        self.model_calls = 0
        self.prompt_tokens = 0
        self._handoff: str | None = None

    def start(self) -> AgentProcess:
        return AgentProcess(self.frozen, self.logs / f"agent_{self.case.id}.log", self.home, self.serve,
                            Sandbox(limits=LIMITED, kit_path=KIT_PATH, passthrough=self.passthrough,
                                    clock=f"{self.case.now}:00" if self.case.now else PRACTICE_CLOCK[self.release]))

    def models(self) -> tuple[tuple[ModelConfig, ...], tuple[ModelConfig, ...]]:
        return self.agent_models, self.embedding_models

    def handed_off(self) -> bool:
        return self._handoff is not None

    def close(self) -> None:
        if self.agent is not None:
            self.agent.close()
        self.console.close()
        self.service.close()
        shutil.rmtree(self.home, ignore_errors=True)

    # ---- the agent's calls

    def serve(self, reply: JsonObject) -> JsonObject:
        match reply.get("call"):
            case "model":
                return self._model(reply)
            case "embed":
                return self._embed(reply)
            case "billing":
                return self._billing(reply)
            case "developer":
                return self.customer.handle(wire_payload(reply), len(self.console_calls))
            case other:
                raise ValueError(f"unknown call {other!r}")

    def _model(self, reply: JsonObject) -> JsonObject:
        request = ModelRequest.model_validate(wire_payload(reply))
        if not set(request.kwargs) <= MODEL_KWARGS:
            raise ValueError(f"unsupported model arguments: {sorted(set(request.kwargs) - MODEL_KWARGS)}")
        resolve_call(self.agent_models, request.model, dict(request.kwargs))  # the same check grading makes
        tools = None if request.tools is None else list[Json](request.tools)
        answer = self.service.call("generate", model=request.model, messages=list[Json](request.messages),
                                   tools=tools, tool_choice=request.tool_choice, kwargs=request.kwargs)
        self.model_calls += 1
        message = answer.get("message")
        if isinstance(message, dict):
            usage = message.get("usage")
            tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            if isinstance(tokens, int):
                self.prompt_tokens += tokens
        return {"message": message}

    def _embed(self, reply: JsonObject) -> JsonObject:
        request = EmbedRequest.model_validate(wire_payload(reply))
        if not set(request.kwargs) <= EMBED_KWARGS:
            raise ValueError(f"unsupported embedding arguments: {sorted(set(request.kwargs) - EMBED_KWARGS)}")
        inputs = checked_inputs(request.inputs)
        resolve_call(self.embedding_models, request.model, dict(request.kwargs))
        return {"vectors": list[Json]([list[Json](vector) for vector in encode(request.model, inputs)])}

    def _billing(self, reply: JsonObject) -> JsonObject:
        entry: JsonObject = {"request": wire_payload(reply)}
        self.console_calls.append(entry)
        answer = self.console.send(reply)
        try:
            body = reply_body(answer)
        except RuntimeError as error:
            entry["error"] = str(error)
            raise
        result = {key: value for key, value in body.items() if key != "ok"}
        entry["result"] = result
        # The console closes the conversation once an escalated case is stored, as the graded store does.
        if reply.get("op") in {"insert", "update"} and reply.get("table") == "cases" \
                and result.get("status") == "escalated":
            self._handoff = str(result.get("case_id") or "case")
        return result


# ---------------------------------------------------------------- reporting


def _short(text: object, limit: int = 300) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + " ..."


def _lines(message: Message) -> list[str]:
    data = to_dict(message)
    role = data.get("role")
    if isinstance(message, MultiToolMessage):
        return [line for tool in message.tool_messages for line in _lines(tool)]
    if isinstance(message, ToolMessage):
        return [f"    tool result: {_short(message.content)}"]
    lines = [f"{'Developer' if role == 'user' else 'Agent'}: {_short(data.get('content'), 1200)}"] \
        if data.get("content") else []
    calls = data.get("tool_calls")
    for call in calls if isinstance(calls, list) else []:
        if isinstance(call, dict):
            lines.append(f"    tool call: {call.get('name')}({_short(json.dumps(call.get('arguments')), 400)})")
    return lines


def report(session: PracticeSession, seconds: float) -> JsonObject:
    return {
        "case_id": session.case.id,
        "termination": session.termination.value,
        "error": session.error,
        "reply": session.reply,
        "seconds": round(seconds, 1),
        "model_calls": session.model_calls,
        "prompt_tokens": session.prompt_tokens,
        "handoff": session._handoff,
        "console_calls": list[Json](session.console_calls),
        "developer_calls": list[Json]([call.model_dump() for call in session.customer.calls]),
        "messages": list[Json]([to_dict(message) for message in session.messages]),
    }


def run_case(case: PracticeCase, frozen: Path, out: Path, release: str) -> JsonObject:
    session = PracticeSession(case, frozen, out, release)
    started = time.monotonic()
    try:
        session.run()
    finally:
        session.close()
    result = report(session, time.monotonic() - started)
    (out / f"{case.id}.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"\n=== {case.id}: {result['termination']}" + (f" ({result['error']})" if result["error"] else ""))
    for message in session.messages:
        for line in _lines(message):
            print(line)
    print(f"--- {len(session.console_calls)} console calls, {session.model_calls} model calls, "
          f"handoff: {result['handoff'] or 'none'}; full record in {out / f'{case.id}.json'}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("scripts", type=Path)
    parser.add_argument("--case", action="append", help="run only this case id (repeatable)")
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    cases = load_cases(args.scripts, args.case)
    release = RELEASE_FILE.read_text().strip()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="redwood-practice-") as scratch:
        frozen = Path(scratch) / "workspace"
        freeze(args.workspace, frozen)
        try:
            for case in cases:
                run_case(case, frozen, args.out, release)
        except PracticeServiceError as error:
            print(f"\npractice service failed (not your agent): {error}", file=sys.stderr)
            return 2
        finally:
            unlock(frozen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
