"""The host side of one support conversation: the agent process, its sandbox copy, and the turn loop.

Grading and practice conversations (`redwood_kit.practice`) both run through this module, so a practice
conversation drives the agent exactly as a graded one does: the same greeting, wire protocol, tool
rounds, step limit, resource limits and endings. Only the services behind the agent's calls differ,
and a session supplies them through `serve`.

`AgentProcess` owns the subprocess running `redwood_kit.candidate` and answers its `model`, `embed`,
`billing` and `developer` calls. `Session` plays one conversation: the greeting, agent turns with tool
rounds, and the developer's replies, until the developer ends it, the agent stops itself, or the
conversation reaches `MAX_STEPS`.
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .agent_context import ModelConfig
from .customer import OUT_OF_SCOPE_TOKEN, STOP_TOKENS, TRANSFER_TOKEN, Speaker
from .jsontypes import Json, JsonObject
from .messages import AssistantMessage, Message, MultiToolMessage, ToolMessage, UserMessage, from_dict, to_dict

GREETING = "Hi! How can I help you today?"
MAX_STEPS = 40  # agent turns, including tool rounds, per conversation
CALL_TIMEOUT = 180
KIT_PATH = "/opt"
MODEL_KWARGS = {"max_output_tokens"}  # the only argument an agent may pass with a model call
EMBED_KWARGS: set[str] = set()

# One thread per library: RLIMIT_NPROC counts threads across every process of the agent's user.
THREADS = {name: "1" for name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS", "POLARS_MAX_THREADS",
)}

# Run by the child itself before any workspace code: preexec_fn is unsafe once conversations run in
# threads, and an unprivileged process cannot raise a hard limit back up. A hard limit already lower than
# the cap is kept (an unprivileged practice run could not raise it; the root grader never meets one).
LIMITS = (
    "import resource\n"
    "for limit, value in ((resource.RLIMIT_NPROC, 1024), (resource.RLIMIT_FSIZE, 128 << 20),\n"
    "                     (resource.RLIMIT_AS, 4 << 30), (resource.RLIMIT_CORE, 0)):\n"
    "    hard = resource.getrlimit(limit)[1]\n"
    "    value = value if hard == resource.RLIM_INFINITY else min(value, hard)\n"
    "    resource.setrlimit(limit, (value, value))\n"
)

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class Termination(StrEnum):
    """How a conversation ended; `finished` splits a real ending from a run that died early."""

    CUSTOMER_STOP = "customer_stop"
    CUSTOMER_TRANSFER = "customer_transfer"
    CUSTOMER_OUT_OF_SCOPE = "customer_out_of_scope"
    AGENT_STOP = "agent_stop"
    MAX_STEPS = "max_steps"
    AGENT_ERROR = "agent_error"

    @property
    def finished(self) -> bool:
        return self not in {Termination.MAX_STEPS, Termination.AGENT_ERROR}


class ModelRequest(BaseModel):
    """A `call:"model"` wire request from the agent, re-validated before serving."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    messages: list[JsonObject] = []
    tools: list[JsonObject] | None = None
    tool_choice: Json = None
    kwargs: JsonObject = {}


class EmbedRequest(BaseModel):
    """A `call:"embed"` wire request from the agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    inputs: str | list[str] = []  # a bare string is refused by checked_inputs with its own message
    kwargs: JsonObject = {}


class TurnReply(BaseModel):
    """The agent's answer to `op: turn`: its message and whether it ends the conversation."""

    message: JsonObject
    stop: bool = False


class ToolsReply(BaseModel):
    """The agent's answer to `op: tools`: one tool message per call, in call order."""

    tool_messages: list[JsonObject]


def wire_payload(reply: JsonObject) -> JsonObject:
    return {key: value for key, value in reply.items() if key != "call"}


# ---------------------------------------------------------------- the sandbox copy


def freeze(workspace: Path, destination: Path) -> None:
    """Copy the workspace to a read-only tree with no links or set-id bits (root-owned when run as root)."""
    if destination.exists():
        _unlock(destination)
        shutil.rmtree(destination)
    shutil.copytree(workspace, destination, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    entries = [destination, *destination.rglob("*")]
    for path in entries:
        _check_safe_entry(path, destination)
    for path in reversed(entries):
        _lock(path)
    for required in ("agent.py", "tools.py"):
        if not (destination / required).is_file():
            raise ValueError(f"workspace/{required} is missing")


def unlock(destination: Path) -> None:
    """Make a frozen tree removable again by its owner."""
    _unlock(destination)


def _unlock(destination: Path) -> None:
    for path in (destination, *destination.rglob("*")):
        if path.is_dir():
            path.chmod(0o700)


def _check_safe_entry(path: Path, destination: Path) -> None:
    relative = path.relative_to(destination)
    if path.is_symlink() or not (path.is_dir() or path.is_file()):
        raise ValueError(f"workspace contains a link or special file: {relative}")
    if path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise ValueError(f"workspace contains set-ID bits: {relative}")


def _lock(path: Path) -> None:
    if os.geteuid() == 0:
        os.chown(path, 0, 0)
    path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)


# ---------------------------------------------------------------- the agent process


Serve = Callable[[JsonObject], JsonObject]


FAKETIME_LIBRARY = "/usr/lib/*/faketime/libfaketime.so.1"


def clock_env(clock: str) -> dict[str, str]:
    """Environment that starts the agent's clock at `clock` ("YYYY-MM-DD HH:MM:SS", UTC) and lets it run forward.

    The conversations happen on their own dates, not the host's, so an agent that reads the time (datetime.now(),
    date.today()) sees the conversation's. Needs libfaketime, which the image installs; on Linux without it the
    run stops rather than give the agent the wrong date. Elsewhere (local tests on macOS) the host clock stays.
    """
    if not clock:
        return {}
    found = sorted(glob.glob(FAKETIME_LIBRARY))
    if not found:
        if sys.platform == "linux":
            raise RuntimeError(f"libfaketime not found at {FAKETIME_LIBRARY}: cannot set the agent's clock")
        return {}
    return {"LD_PRELOAD": found[0], "FAKETIME": f"@{clock}", "FAKETIME_DONT_FAKE_MONOTONIC": "1", "TZ": "UTC"}


@dataclass(frozen=True)
class Sandbox:
    """How the agent process runs: as `user` when the host is root, with `limits` on its resources, importing the
    kit from `kit_path`, its clock starting at `clock` (empty: the host's). Errors of a `passthrough` type mean the
    run itself failed and propagate."""

    user: pwd.struct_passwd | None = None
    limits: bool = True
    kit_path: str = KIT_PATH
    passthrough: tuple[type[BaseException], ...] = ()
    clock: str = ""


class AgentProcess:
    """The subprocess running redwood_kit.candidate from a frozen workspace.

    `serve` answers each call the agent makes (`model`, `embed`, `billing`, `developer`) with the reply
    body; an exception becomes an `ok: false` reply the agent sees, except the sandbox's passthrough types.
    """

    def __init__(self, workspace: Path, log: Path, home: Path, serve: Serve, sandbox: Sandbox) -> None:
        self.serve = serve
        self.passthrough = sandbox.passthrough
        self.position = 0  # set by the session: the position of the current request in the conversation
        self.log = log.open("a")
        user = sandbox.user
        start = f"import sys; sys.path.insert(0, {sandbox.kit_path!r}); from redwood_kit.candidate import main; main()"
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", (LIMITS if sandbox.limits else "") + start],
            cwd=workspace,
            env={"HOME": str(home), "TMPDIR": str(home), "LANG": "C.UTF-8", "PATH": "/usr/local/bin:/usr/bin:/bin",
                 **THREADS, **clock_env(sandbox.clock)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, bufsize=1, start_new_session=True,
            user=user.pw_uid if user else None, group=user.pw_gid if user else None,
            extra_groups=() if user else None,
        )

    def close(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):  # macOS: EPERM for an exited group
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=10)
        self.log.close()

    def _write(self, payload: JsonObject) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def _read(self) -> JsonObject:
        timer = threading.Timer(CALL_TIMEOUT, lambda: os.killpg(self.process.pid, signal.SIGKILL))
        timer.start()
        try:
            assert self.process.stdout is not None
            line = self.process.stdout.readline()
        finally:
            timer.cancel()
        if not line:
            raise RuntimeError("candidate process exited or timed out")
        return _JSON_OBJECT.validate_json(line)

    def request(self, payload: JsonObject) -> JsonObject:
        """Send one request and serve the agent's calls until its reply arrives."""
        self._write(payload)
        while (reply := self._read()).get("call"):
            try:
                self._write({"ok": True, **self.serve(reply)})
            except self.passthrough:
                raise  # not the agent's doing: the run is invalid
            except Exception as error:
                self._write({"ok": False, "error": str(error)[:2000]})
        if not reply.get("ok"):
            raise RuntimeError(f"candidate error: {reply.get('error')}")
        return reply


# ---------------------------------------------------------------- the conversation


def _stopped(message: Message) -> bool:
    return isinstance(message, UserMessage) and any(token in (message.content or "") for token in STOP_TOKENS)


_CUSTOMER_ENDINGS = (
    (TRANSFER_TOKEN, Termination.CUSTOMER_TRANSFER),
    (OUT_OF_SCOPE_TOKEN, Termination.CUSTOMER_OUT_OF_SCOPE),
)


def _customer_termination(message: Message) -> Termination:
    text = "" if isinstance(message, MultiToolMessage) else message.content or ""
    return next((ending for token, ending in _CUSTOMER_ENDINGS if token in text), Termination.CUSTOMER_STOP)


def _tool_message(item: JsonObject) -> ToolMessage:
    message = from_dict(item)
    if not isinstance(message, ToolMessage):
        raise TypeError("expected a tool message")
    return message


class Session:
    """One conversation: greeting, agent turns with tool rounds, and the developer's replies.

    A subclass supplies the agent process (`start`), the models it may call, and whether the
    conversation has been handed to a team. `run` ends when the developer sends a stop token, the
    agent stops itself, or the conversation hits `MAX_STEPS`; an error of a `passthrough` type
    propagates (an invalid run), any other becomes `agent_error`.
    """

    passthrough: tuple[type[BaseException], ...] = ()

    def __init__(self, customer: Speaker) -> None:
        self.customer = customer
        self.max_steps = MAX_STEPS
        self.messages: list[Message] = [AssistantMessage(content=GREETING)]
        self.agent: AgentProcess | None = None
        self.termination = Termination.AGENT_ERROR
        self.reply: str | None = None  # the agent's last text reply
        self.error: str | None = None

    # ---- supplied by a subclass

    def start(self) -> AgentProcess:
        raise NotImplementedError

    def models(self) -> tuple[tuple[ModelConfig, ...], tuple[ModelConfig, ...]]:
        """(agent models, embedding models) the agent is told it may call."""
        raise NotImplementedError

    def handed_off(self) -> bool:
        raise NotImplementedError

    # ---- the loop

    def run(self) -> None:
        try:
            self._converse()
        except self.passthrough:
            raise
        except Exception as error:
            self.termination = Termination.AGENT_ERROR
            self.error = f"{type(error).__name__}: {error}"[:4000]

    def _converse(self) -> None:
        pending = self._open()
        steps = 0
        while not _stopped(pending):
            if steps == self.max_steps:
                self.termination = Termination.MAX_STEPS
                return
            steps += 1
            customer_message = self._turn(pending)
            if customer_message is None:
                return  # the agent stopped itself
            pending = customer_message
        self.termination = _customer_termination(pending)

    def _open(self) -> Message:
        self.agent = self.start()
        models, embedding_models = self.models()
        self.agent.request({"op": "init", "models": [asdict(m) for m in models],
                            "embedding_models": [asdict(m) for m in embedding_models],
                            "first_message": to_dict(self.messages[0])})
        pending = self.customer.reply(GREETING, handed_off=False)
        self.messages.append(pending)
        return pending

    def _turn(self, pending: Message) -> Message | None:
        """One agent turn plus any tool rounds; returns the next developer message, or None on agent stop."""
        agent = self._agent()
        agent.position = len(self.messages)
        turn = TurnReply.model_validate(agent.request({"op": "turn", "message": to_dict(pending)}))
        reply = from_dict(turn.message)
        self.messages.append(reply)
        if not isinstance(reply, AssistantMessage) or reply.has_text_content() == reply.is_tool_call():
            raise RuntimeError("agent returned a message that is neither text nor tool calls")
        next_pending = self._run_tool_calls(reply) if reply.is_tool_call() else None
        if not reply.is_tool_call():
            self.reply = reply.content
        if turn.stop:  # after any tool calls on the closing message have run
            self.termination = Termination.AGENT_STOP
            return None
        if next_pending is not None:
            return next_pending
        answer = self.customer.reply(reply.content or "", handed_off=self.handed_off())
        self.messages.append(answer)
        return answer

    def _run_tool_calls(self, reply: AssistantMessage) -> Message:
        agent = self._agent()
        agent.position = len(self.messages)
        outputs = ToolsReply.model_validate(
            agent.request({"op": "tools", "tool_calls": [asdict(c) for c in reply.tool_calls or []]}))
        tool_messages = [_tool_message(item) for item in outputs.tool_messages]
        self.messages.extend(tool_messages)
        return tool_messages[0] if len(tool_messages) == 1 else MultiToolMessage(tool_messages)

    def _agent(self) -> AgentProcess:
        if self.agent is None:
            raise RuntimeError("conversation has no agent process")
        return self.agent
