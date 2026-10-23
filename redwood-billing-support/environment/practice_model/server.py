"""The practice model service: the deployed support model and the developer's wording model, for practice only.

Runs in its own container (environment/docker-compose.yaml) with the provider credentials, which never
enter the builder's container. The builder's practice runner (`redwood_kit.practice`) reaches it over
a Unix socket on a volume both containers mount. It serves two calls, each through the grading code
itself (`redwood_kit.gateway`, which grading also uses), so practice answers come
from the same model, settings and request shape as graded conversations:

    {"op": "open", "release"}
        start one practice conversation; returns its `conversation` id
    {"op": "generate", "conversation", "release", "model", "messages", "tools", "tool_choice", "kwargs"}
        one agent model call, checked like a graded one (manifest model, allowed arguments)
    {"op": "say", "conversation", "release", "persona", "line", "approval", "transcript"}
        one developer line worded with the grading rules (redwood_kit.customer.RULES); the
        instructions are built here, so this is not a general completion endpoint

Budget: REDWOOD_PRACTICE_CONVERSATIONS conversations per release (default 5), each with at most
REDWOOD_PRACTICE_MODEL_CALLS model calls and REDWOOD_PRACTICE_SAY_CALLS wordings. Every call is logged,
without its content, to practice-model.log next to the socket.
"""
from __future__ import annotations

import json
import os
import socketserver
import threading
import time
from pathlib import Path

from redwood_kit import gateway  # noqa: E402
from redwood_kit.agent_context import resolve_call  # noqa: E402
from redwood_kit.conversation import ModelRequest  # noqa: E402
from redwood_kit.customer import instructions  # noqa: E402
from redwood_kit.gateway import ProviderError  # noqa: E402
from redwood_kit.jsontypes import JsonObject  # noqa: E402
from redwood_kit.messages import from_dict, to_dict  # noqa: E402

SOCKET = Path(os.environ.get("REDWOOD_PRACTICE_SOCKET", "/run/redwood-model/practice-model.sock"))
LOG = SOCKET.with_name("practice-model.log")
CUSTOMER_MODEL = gateway.USER_MODEL
RELEASES = ("R0", "R1")
CONVERSATIONS = int(os.environ.get("REDWOOD_PRACTICE_CONVERSATIONS", "5"))  # per release
BUDGETS = {"generate": int(os.environ.get("REDWOOD_PRACTICE_MODEL_CALLS", "60")),  # per conversation
           "say": int(os.environ.get("REDWOOD_PRACTICE_SAY_CALLS", "40"))}
CUSTOMER_EFFORT = "low"  # what gateway.complete uses for the graded developer


class PracticeError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Budget:
    """Conversations per release, and calls per conversation."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.opened = dict.fromkeys(RELEASES, 0)
        self.calls: dict[str, dict[str, int]] = {}  # conversation id -> op -> calls made

    def open(self, release: str) -> str:
        with self.lock:
            if self.opened[release] >= CONVERSATIONS:
                raise PracticeError("budget", f"all {CONVERSATIONS} practice conversations for {release} are used")
            self.opened[release] += 1
            conversation = f"{release}-{self.opened[release]}"
            self.calls[conversation] = dict.fromkeys(BUDGETS, 0)
            return conversation

    def spend(self, op: str, release: str, conversation: object) -> int:
        with self.lock:
            calls = self.calls.get(str(conversation)) if str(conversation).startswith(f"{release}-") else None
            if calls is None:
                raise PracticeError("request", "open a practice conversation first (op: open)")
            if calls[op] >= BUDGETS[op]:
                raise PracticeError("budget", f"this practice conversation used its {BUDGETS[op]} {op} calls")
            calls[op] += 1
            return calls[op]

    def refund(self, release: str, conversation: object) -> None:
        """A provider failure is not the builder's: the conversation it broke does not count."""
        with self.lock:
            if self.calls.pop(str(conversation), None) is not None and self.opened[release] > 0:
                self.opened[release] -= 1


BUDGET = Budget()
_LOG_LOCK = threading.Lock()


def _log(entry: JsonObject) -> None:
    with _LOG_LOCK, LOG.open("a") as file:
        file.write(json.dumps({"at": round(time.time(), 3), **entry}) + "\n")


def _release(request: JsonObject) -> str:
    release = request.get("release")
    if release not in RELEASES:
        raise PracticeError("request", f"release must be one of {list(RELEASES)}")
    return str(release)


def _generate(request: JsonObject) -> JsonObject:
    """One agent model call, served exactly as run_agent.Candidate serves a graded one."""
    try:
        call = ModelRequest.model_validate({k: v for k, v in request.items()
                                            if k not in {"op", "release", "conversation"}})
        if not set(call.kwargs) <= gateway.ALLOWED_KWARGS:
            raise ValueError(f"unsupported model arguments: {sorted(set(call.kwargs) - gateway.ALLOWED_KWARGS)}")
        kwargs = resolve_call(gateway.MODELS, call.model, dict(call.kwargs))
        messages = [from_dict(message) for message in call.messages]
    except (ValueError, TypeError, KeyError) as error:
        raise PracticeError("request", str(error)[:2000]) from None
    message = gateway.generate(call.model, messages, call.tools, call.tool_choice, **kwargs)
    return {"message": to_dict(message)}


def _say(request: JsonObject) -> JsonObject:
    """One developer line, worded with the grading rules; text is null when no wording model is set."""
    fields = {name: request.get(name) for name in ("persona", "line", "approval", "transcript")}
    if not all(isinstance(value, str) for value in fields.values()) or not str(fields["line"]).strip():
        raise PracticeError("request", "say needs persona, line, approval and transcript as strings")
    model = os.environ.get(CUSTOMER_MODEL, "")
    if not model:
        return {"text": None}
    text = gateway.complete(model, instructions(str(fields["persona"]), str(fields["line"]),
                                                       str(fields["approval"])),
                            str(fields["transcript"]), CUSTOMER_EFFORT)
    return {"text": text}


HANDLERS = {"generate": _generate, "say": _say}


def answer(request: object) -> JsonObject:
    """The reply to one request line; never raises."""
    started, entry = time.monotonic(), {}
    try:
        if not isinstance(request, dict) or request.get("op") not in {*HANDLERS, "open"}:
            raise PracticeError("request", f"op must be one of {sorted({*HANDLERS, 'open'})}")
        op, release = str(request["op"]), _release(request)
        entry = {"op": op, "release": release, "model": request.get("model"),
                 "conversation": request.get("conversation")}
        if op == "open":
            reply: JsonObject = {"ok": True, "conversation": BUDGET.open(release)}
        else:
            entry["call"] = BUDGET.spend(op, release, request.get("conversation"))
            reply = {"ok": True, **HANDLERS[op](request)}
    except PracticeError as error:
        reply = {"ok": False, "kind": error.kind, "error": str(error)}
    except ProviderError as error:
        if isinstance(request, dict) and request.get("release") in RELEASES:
            BUDGET.refund(str(request["release"]), request.get("conversation"))
        reply = {"ok": False, "kind": "provider",
                 "error": f"the model provider failed (this practice conversation is not counted): {error}"[:2000]}
    except Exception as error:  # noqa: BLE001 - reported to the caller, never kills the service
        reply = {"ok": False, "kind": "service", "error": f"{type(error).__name__}: {error}"[:2000]}
    _log({**entry, "ok": reply["ok"], "kind": reply.get("kind"), "seconds": round(time.monotonic() - started, 2)})
    return reply


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for line in self.rfile:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                request = None
            self.wfile.write(json.dumps(answer(request)).encode() + b"\n")
            self.wfile.flush()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve() -> None:
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), _Handler) as server:
        SOCKET.chmod(0o666)  # the builder's runner connects as its own user
        LOG.touch()
        LOG.chmod(0o644)
        print(f"practice model service on {SOCKET}: support {gateway.SUPPORT_MODEL}, developer wording "
              f"{os.environ.get(CUSTOMER_MODEL) or 'off'}, {CONVERSATIONS} conversations per release, "
              f"{BUDGETS} per conversation", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    serve()
