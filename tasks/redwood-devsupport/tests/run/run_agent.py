"""Run one simulated developer conversation against a frozen support agent.

The agent runs as `nobody` from a read-only copy of the workspace. Every model
call and every embedding call comes back to this process, which checks it
against the allowed models and sends it to OpenAI; tool calls run inside the
agent's sandbox. Calls to Redwood's billing system also come back here, into a
store only this process writes. A model plays the developer from a hidden
scenario and ends the conversation with a stop token.

Pieces: `Conversation` drives one case end to end and produces a `Result`.
`Candidate` owns the agent subprocess and the wire protocol to it.
`freeze`/`scrub` prepare and clean the sandbox. The developer is played by
run/customer.py and model calls go out through run/gateway.py.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path

from grade.scenarios import Release, Scenario
from pydantic import BaseModel, ConfigDict, TypeAdapter
from redwood_kit.agent_context import resolve_call
from redwood_kit.billing.schema import State
from redwood_kit.billing.store import Billing, ConsoleCall, validate
from redwood_kit.embeddings import checked_inputs
from redwood_kit.jsontypes import Json, JsonObject
from redwood_kit.messages import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
    from_dict,
    to_dict,
)
from run import gateway
from run.customer import OUT_OF_SCOPE_TOKEN, STOP_TOKENS, TRANSFER_TOKEN, CustomerError, Speaker
from run.gateway import json_list

GREETING = "Hi! How can I help you today?"
MAX_STEPS = 40  # agent turns, including tool rounds, per conversation
CALL_TIMEOUT = 180
KIT_PATH = "/opt"
DB_PATHS = (Path("/opt/redwood-data/db.json"), Path(__file__).resolve().parents[2] / "data" / "db.json")
DB_R1_PATHS = (Path("/opt/redwood-overlays/R1/db_r1.json"), Path(__file__).resolve().parents[2] / "data" / "db_r1.json")


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
    messages: list[JsonObject] = []  # wire-format message dicts: they go to the judge and the case log
    model_calls: list[ModelCall] = []
    embedding_calls: list[EmbeddingCall] = []
    billing: BillingRecord = BillingRecord()
    seconds: float = 0.0


_STATE: TypeAdapter[State] = TypeAdapter(State)
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class TurnReply(BaseModel):
    """The candidate's answer to `op: turn`: the agent's message and whether it ends the conversation."""

    message: JsonObject
    stop: bool = False


class ToolsReply(BaseModel):
    """The candidate's answer to `op: tools`: one tool message per call, in call order."""

    tool_messages: list[JsonObject]


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


# One thread per library: RLIMIT_NPROC counts threads across every case running as nobody.
THREADS = {name: "1" for name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS", "POLARS_MAX_THREADS",
)}

def freeze(workspace: Path, destination: Path) -> None:
    """Copy the workspace to a root-owned, read-only tree with no links or set-id bits."""
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


# Run by the child itself before any workspace code: preexec_fn is unsafe once the grader runs cases in
# threads, and an unprivileged process cannot raise a hard limit back up.
LIMITS = (
    "import resource\n"
    "for limit, value in ((resource.RLIMIT_NPROC, 1024), (resource.RLIMIT_FSIZE, 128 << 20),\n"
    "                     (resource.RLIMIT_AS, 4 << 30), (resource.RLIMIT_CORE, 0)):\n"
    "    resource.setrlimit(limit, (value, value))\n"
)


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


class ModelRequest(BaseModel):
    """A `call:"model"` wire request from the candidate, re-validated before serving."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    messages: list[JsonObject] = []
    tools: list[JsonObject] | None = None
    tool_choice: Json = None
    kwargs: JsonObject = {}


class EmbedRequest(BaseModel):
    """A `call:"embed"` wire request from the candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    inputs: str | list[str] = []  # a bare string is refused by checked_inputs with its own message
    kwargs: JsonObject = {}


def _wire_payload(reply: JsonObject) -> JsonObject:
    return {key: value for key, value in reply.items() if key != "call"}


class Candidate:
    """The `nobody` subprocess running redwood_kit.candidate; serves model and billing calls."""

    def __init__(self, workspace: Path, log: Path, home: Path, release: Release = Release.R0) -> None:
        self.model_calls: list[ModelCall] = []
        self.embedding_calls: list[EmbeddingCall] = []
        self.billing = Billing(load_db(release), release=Release(release).value)
        self.position = 0  # set by the conversation: the position of the current request
        self.log = log.open("a")
        nobody = pwd.getpwnam("nobody") if os.geteuid() == 0 else None
        start = f"import sys; sys.path.insert(0, {KIT_PATH!r}); from redwood_kit.candidate import main; main()"
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", (LIMITS if nobody else "") + start],
            cwd=workspace,
            env={"HOME": str(home), "TMPDIR": str(home), "LANG": "C.UTF-8", "PATH": "/usr/local/bin:/usr/bin:/bin",
                 **THREADS},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, bufsize=1, start_new_session=True,
            # The verifier runs as root: drop to nobody (LIMITS caps resources). Local runs stay unprivileged.
            user=nobody.pw_uid if nobody else None, group=nobody.pw_gid if nobody else None,
            extra_groups=() if nobody else None,
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

    def _serve_model(self, reply: JsonObject) -> JsonObject:
        request = ModelRequest.model_validate(_wire_payload(reply))
        if not set(request.kwargs) <= gateway.ALLOWED_KWARGS:
            raise ValueError(f"unsupported model arguments: {sorted(set(request.kwargs) - gateway.ALLOWED_KWARGS)}")
        kwargs = resolve_call(gateway.MODELS, request.model, dict(request.kwargs))  # the candidate is untrusted
        messages = [from_dict(message) for message in request.messages]
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
        request = EmbedRequest.model_validate(_wire_payload(reply))
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
            case other:
                raise ValueError(f"unknown call {other!r}")

    def request(self, payload: JsonObject) -> JsonObject:
        """Send one request and serve model and billing calls until the reply arrives."""
        self._write(payload)
        while (reply := self._read()).get("call"):
            try:
                self._write({"ok": True, **self._dispatch(reply)})
            except Exception as error:
                self._write({"ok": False, "error": str(error)[:2000]})
        if not reply.get("ok"):
            raise RuntimeError(f"candidate error: {reply.get('error')}")
        return reply


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


class Conversation:
    """One simulated case: greeting, agent turns with tool rounds, then the wrap-up.

    The loop ends when the developer sends a stop token, the agent stops
    itself, or the conversation hits `MAX_STEPS`. `CustomerError` propagates
    (an invalid run); anything else becomes `agent_error` on the result.
    """

    def __init__(self, case: Scenario, workspace: Path, log_dir: Path, customer: Speaker,
                 release: Release) -> None:
        self.result = Result(case_id=case.id)
        self.messages: list[Message] = [AssistantMessage(content=GREETING)]
        self.workspace = workspace
        self.log_dir = log_dir
        self.customer = customer
        self.release = Release(release)
        self.candidate: Candidate | None = None
        self.home = Path(tempfile.mkdtemp(prefix="redwood-case-"))  # private scratch, deleted after
        if os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            os.chown(self.home, nobody.pw_uid, nobody.pw_gid)

    def run(self) -> Result:
        started = time.monotonic()
        try:
            self._converse()
        except CustomerError:
            raise
        except Exception as error:
            self.result.termination = Termination.AGENT_ERROR
            self.result.error = f"{type(error).__name__}: {error}"[:4000]
        finally:
            self._collect(started)
        return self.result

    def _converse(self) -> None:
        pending = self._open()
        steps = 0
        while not _stopped(pending):
            if steps == MAX_STEPS:
                self.result.termination = Termination.MAX_STEPS
                return
            steps += 1
            customer_message = self._turn(pending)
            if customer_message is None:
                return  # the agent stopped itself
            pending = customer_message
        self.result.termination = _customer_termination(pending)

    def _open(self) -> Message:
        self.candidate = Candidate(self.workspace, self.log_dir / f"agent_{self.result.case_id}.log",
                                   self.home, self.release)
        self.candidate.request({"op": "init", "models": [asdict(m) for m in gateway.MODELS],
                                "embedding_models": [asdict(m) for m in gateway.EMBEDDING_MODELS],
                                "first_message": to_dict(self.messages[0])})
        pending = self.customer.reply(GREETING)
        self.messages.append(pending)
        return pending

    def _turn(self, pending: Message) -> Message | None:
        """One agent turn plus any tool rounds; returns the next customer message, or None on agent stop."""
        candidate = self._candidate()
        candidate.position = len(self.messages)
        turn = TurnReply.model_validate(candidate.request({"op": "turn", "message": to_dict(pending)}))
        reply = from_dict(turn.message)
        self.messages.append(reply)
        if not isinstance(reply, AssistantMessage) or reply.has_text_content() == reply.is_tool_call():
            raise RuntimeError("agent returned a message that is neither text nor tool calls")
        next_pending = self._run_tool_calls(reply) if reply.is_tool_call() else None
        if not reply.is_tool_call():
            self.result.reply = reply.content
        if turn.stop:  # after any tool calls on the closing message have run
            self.result.termination = Termination.AGENT_STOP
            return None
        if next_pending is not None:
            return next_pending
        answer = self.customer.reply(reply.content or "")
        self.messages.append(answer)
        return answer

    def _run_tool_calls(self, reply: AssistantMessage) -> Message:
        candidate = self._candidate()
        candidate.position = len(self.messages)
        outputs = ToolsReply.model_validate(
            candidate.request({"op": "tools", "tool_calls": [asdict(c) for c in reply.tool_calls or []]}))
        tool_messages = [_tool_message(item) for item in outputs.tool_messages]
        self.messages.extend(tool_messages)
        return tool_messages[0] if len(tool_messages) == 1 else MultiToolMessage(tool_messages)

    def _candidate(self) -> Candidate:
        if self.candidate is None:
            raise RuntimeError("conversation has no candidate")
        return self.candidate

    def _collect(self, started: float) -> None:
        if self.candidate is not None:
            self.result.model_calls = self.candidate.model_calls
            self.result.embedding_calls = self.candidate.embedding_calls
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
    """Converse until the customer ends it or the agent runs out of steps. CustomerError propagates."""
    return Conversation(case, workspace, log_dir, customer, release).run()
