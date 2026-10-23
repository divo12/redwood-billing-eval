"""Run one simulated developer conversation against a frozen support agent.

The agent runs as `nobody` from a read-only copy of the workspace. Every model
call and every embedding call comes back to this process, which checks it
against the allowed models and sends it to the provider; tool calls run inside the
agent's sandbox. Calls to Redwood's billing system also come back here, into a
store only this process writes, and so do the agent's questions to the developer.
A fixed script plays the developer from a hidden scenario and ends the
conversation with a stop token.

The conversation loop, the agent process and the sandbox copy are
redwood_kit.conversation, shared with the builder's practice runner so practice
conversations run exactly like graded ones. This module adds what only grading
has: the hidden scenario, the in-process billing store, the evidence each
conversation records (`Result`), and the cleanup after it (`scrub`).
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import pwd
import shutil
import signal
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from grade.scenarios import Release, Scenario
from pydantic import BaseModel, TypeAdapter
from redwood_kit.agent_context import ModelConfig, resolve_call
from redwood_kit.billing.schema import State
from redwood_kit.billing.store import Billing, ConsoleCall, validate
from redwood_kit.conversation import (
    CALL_TIMEOUT,
    GREETING,
    LIMITS,
    THREADS,
    AgentProcess,
    EmbedRequest,
    ModelRequest,
    Sandbox,
    Session,
    Termination,
    ToolsReply,
    TurnReply,
    freeze,
    wire_payload,
)
from redwood_kit.customer import DeveloperCall, Speaker
from redwood_kit.embeddings import checked_inputs
from redwood_kit.jsontypes import Json, JsonObject
from redwood_kit.messages import Message, SystemMessage, from_dict, to_dict
from run import gateway
from run.gateway import json_list
from run.provider import ProviderError

MAX_STEPS = 40  # agent turns, including tool rounds, per conversation
KIT_PATH = "/opt"
DB_PATHS = (Path("/opt/redwood-data/db.json"), Path(__file__).resolve().parents[2] / "data" / "db.json")
DB_R1_PATHS = (Path("/opt/redwood-overlays/R1/db_r1.json"), Path(__file__).resolve().parents[2] / "data" / "db_r1.json")


class ModelCall(BaseModel):
    """One model request the agent made, re-validated and recorded for evidence."""

    model: str
    kwargs: JsonObject
    instructions_chars: int
    tools: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    messages: list[JsonObject]
    response: JsonObject


class EmbeddingCall(BaseModel):
    model: str
    kwargs: JsonObject
    inputs: int
    prompt_tokens: int


class BillingRecord(BaseModel):
    """The billing world at the end of a conversation: final state and every console call."""

    state: State = {}
    log: tuple[ConsoleCall, ...] = ()


class Result(BaseModel):
    """Everything a conversation produced: how it ended, the transcript, and the side effects."""

    case_id: str
    termination: Termination = Termination.AGENT_ERROR
    reply: str | None = None  # the agent's last text reply
    error: str | None = None
    messages: list[JsonObject] = []  # wire-format message dicts: they go to the graders and the case log
    model_calls: list[ModelCall] = []
    embedding_calls: list[EmbeddingCall] = []
    developer_calls: list[DeveloperCall] = []
    billing: BillingRecord = BillingRecord()
    seconds: float = 0.0
    # Why this conversation says nothing about the agent (the provider failed, the developer leaked an answer).
    # Such a case is listed, left out of the pass rates, and regraded.
    invalid: str | None = None


# Shared names the grader and its tests import from here.
__all__ = ["CALL_TIMEOUT", "GREETING", "LIMITS", "THREADS", "Termination", "ToolsReply", "TurnReply", "freeze"]

_STATE: TypeAdapter[State] = TypeAdapter(State)


def _read_db(paths: tuple[Path, ...]) -> tuple[Json, Path]:
    source = next((path for path in paths if path.is_file()), None)
    if source is None:
        raise FileNotFoundError(f"billing database not found at any of: {', '.join(map(str, paths))}")
    try:
        return json.loads(source.read_text()), source
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{source}: {error}") from error


def load_db(release: Release = Release.R0) -> State:
    """The shared billing seed: `db.json` alone for R0, plus the `db_r1.json` delta for R1."""
    raw, source = _read_db(DB_PATHS)
    validate(raw, source)
    state = _STATE.validate_python(raw)
    if Release(release) is Release.R0:
        return copy.deepcopy(state)
    raw_delta, delta_source = _read_db(DB_R1_PATHS)
    delta = _STATE.validate_python(raw_delta)
    for table, rows in delta.items():
        for key, row in rows.items():
            if key in state[table]:
                raise ValueError(f"{delta_source}: {table}.{key} already exists in {source}")
            state[table][key] = row
    validate(state, delta_source)
    return copy.deepcopy(state)


def scrub(user: str = "nobody") -> int:
    """Kill the agent user's processes and delete every file it owns anywhere.

    The container is shared with the builder's next session, which must not
    find anything the agent wrote while talking to the hidden developers.
    """
    uid = pwd.getpwnam(user).pw_uid
    for entry in Path("/proc").iterdir():
        with contextlib.suppress(OSError, ValueError):
            if entry.name.isdigit() and entry.stat().st_uid == uid:
                os.kill(int(entry.name), signal.SIGKILL)
    return remove_owned(uid, (Path("/"), Path("/dev/shm")), skip=(Path("/proc"), Path("/sys"), Path("/dev")))


def remove_owned(uid: int, roots: tuple[Path, ...], skip: tuple[Path, ...] = ()) -> int:
    removed = 0
    for root in roots:
        for directory, subdirectories, files in os.walk(root):
            here, keep = Path(directory), []
            for name in subdirectories:
                path = here / name
                with contextlib.suppress(FileNotFoundError):
                    if path in skip:
                        continue
                    if path.lstat().st_uid != uid:
                        keep.append(name)
                        continue
                    path.unlink() if path.is_symlink() else shutil.rmtree(path)
                    removed += 1
            subdirectories[:] = keep
            for name in files:
                with contextlib.suppress(FileNotFoundError):
                    if (here / name).lstat().st_uid == uid:
                        (here / name).unlink()
                        removed += 1
    return removed


REPLAY_HINT = "REDWOOD_REPLAY_HINT"  # diagnosis only: the rule stated plainly, to tell knowledge from application


def with_replay_hint(messages: list[Message]) -> list[Message]:
    """The agent's messages with REDWOOD_REPLAY_HINT added to its system message, as guidance from the support
    team (never as developer text, which agents rightly distrust). Unset in graded runs: the messages as sent."""
    hint = os.environ.get(REPLAY_HINT, "").strip()
    if not hint:
        return messages
    guidance = f"Guidance from the support team for this conversation (not from the developer).\n\n{hint}"
    for i, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            return [*messages[:i], SystemMessage(content=f"{message.content or ''}\n\n{guidance}"), *messages[i + 1:]]
    return [SystemMessage(content=guidance), *messages]


class World(NamedTuple):
    """Where and when a graded conversation happens: the console release, and the agent's clock (empty: the host's)."""

    release: Release = Release.R0
    clock: str = ""


class Candidate(AgentProcess):
    """The agent process for a graded conversation: serves model and embedding calls through the gateway,
    billing calls from an in-process store seeded with the release's records, and developer calls."""

    def __init__(self, workspace: Path, log: Path, home: Path, world: World,
                 developer: Callable[[JsonObject, int], JsonObject] | None = None) -> None:
        release, clock = world
        self.developer = developer
        self.model_calls: list[ModelCall] = []
        self.embedding_calls: list[EmbeddingCall] = []
        self.billing = Billing(load_db(release), release=Release(release).value)
        # The verifier runs as root: drop to nobody, with the resource limits. Local runs stay unprivileged.
        nobody = pwd.getpwnam("nobody") if os.geteuid() == 0 else None
        super().__init__(workspace, log, home, self._dispatch,
                         Sandbox(nobody, limits=nobody is not None, kit_path=KIT_PATH, passthrough=(ProviderError,),
                                 clock=clock))

    def _serve_model(self, reply: JsonObject) -> JsonObject:
        request = ModelRequest.model_validate(wire_payload(reply))
        if not set(request.kwargs) <= gateway.ALLOWED_KWARGS:
            raise ValueError(f"unsupported model arguments: {sorted(set(request.kwargs) - gateway.ALLOWED_KWARGS)}")
        kwargs = resolve_call(gateway.MODELS, request.model, dict(request.kwargs))  # the candidate is untrusted
        messages = with_replay_hint([from_dict(message) for message in request.messages])
        message = gateway.generate(request.model, messages, request.tools, request.tool_choice, **kwargs)
        usage = message.usage or {}
        instructions = next((m.content for m in messages if isinstance(m, SystemMessage)), "") or ""
        self.model_calls.append(ModelCall(
            model=request.model, kwargs=kwargs, instructions_chars=len(instructions),
            tools=len(request.tools or []), prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            messages=[to_dict(m) for m in messages], response=to_dict(message)))
        return {"message": to_dict(message)}

    def _serve_embed(self, reply: JsonObject) -> JsonObject:
        request = EmbedRequest.model_validate(wire_payload(reply))
        if not set(request.kwargs) <= gateway.EMBED_KWARGS:
            raise ValueError(f"unsupported embedding arguments: {sorted(set(request.kwargs) - gateway.EMBED_KWARGS)}")
        inputs = checked_inputs(request.inputs)
        kwargs = resolve_call(gateway.EMBEDDING_MODELS, request.model, dict(request.kwargs))
        vectors, tokens = gateway.embed(request.model, inputs, **kwargs)
        self.embedding_calls.append(EmbeddingCall(model=request.model, kwargs=kwargs,
                                                  inputs=len(inputs), prompt_tokens=tokens))
        return {"vectors": json_list([json_list(vector) for vector in vectors])}

    def _dispatch(self, reply: JsonObject) -> JsonObject:
        match reply.get("call"):
            case "model":
                return self._serve_model(reply)
            case "embed":
                return self._serve_embed(reply)
            case "billing":
                return self.billing.handle(reply, self.position)
            case "developer" if self.developer is not None:
                return self.developer(wire_payload(reply), len(self.billing.log))
            case other:
                raise ValueError(f"unknown call {other!r}")


class Conversation(Session):
    """One simulated case (redwood_kit.conversation.Session) with the evidence grading keeps.

    A ProviderError propagates (an invalid run); any other error becomes `agent_error` on the result.
    """

    passthrough = (ProviderError,)

    def __init__(self, case: Scenario, workspace: Path, log_dir: Path, customer: Speaker,
                 release: Release) -> None:
        super().__init__(customer)
        self.max_steps = MAX_STEPS
        self.result = Result(case_id=case.id)
        self.clock = case.clock
        self.workspace = workspace
        self.log_dir = log_dir
        self.release = Release(release)
        self.candidate: Candidate | None = None
        self.home = Path(tempfile.mkdtemp(prefix="redwood-case-"))  # private scratch, deleted after
        if os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            os.chown(self.home, nobody.pw_uid, nobody.pw_gid)

    def start(self) -> Candidate:
        self.candidate = Candidate(self.workspace, self.log_dir / f"agent_{self.result.case_id}.log",
                                   self.home, World(self.release, self.clock), developer=self.customer.handle)
        return self.candidate

    def models(self) -> tuple[tuple[ModelConfig, ...], tuple[ModelConfig, ...]]:
        return gateway.MODELS, gateway.EMBEDDING_MODELS

    def handed_off(self) -> bool:
        return self.candidate is not None and self.candidate.billing.handoff is not None

    def run(self) -> Result:  # type: ignore[override]
        started = time.monotonic()
        try:
            super().run()
        finally:
            self._collect(started)
        return self.result

    def _collect(self, started: float) -> None:
        self.result.termination, self.result.reply, self.result.error = self.termination, self.reply, self.error
        if self.candidate is not None:
            self.result.model_calls = self.candidate.model_calls
            self.result.embedding_calls = self.candidate.embedding_calls
            self.result.developer_calls = list(self.customer.calls)
            self.result.billing = BillingRecord(
                state=self.candidate.billing.state,
                log=tuple(self.candidate.billing.log),
            )
            self.candidate.close()
        shutil.rmtree(self.home, ignore_errors=True)
        self.result.messages = [to_dict(m) for m in self.messages]
        self.result.seconds = time.monotonic() - started


def run_case(case: Scenario, workspace: Path, log_dir: Path, customer: Speaker,
             release: Release = Release.R0) -> Result:
    """Converse until the customer ends it or the agent runs out of steps."""
    return Conversation(case, workspace, log_dir, customer, release).run()
