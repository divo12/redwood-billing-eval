"""Score retrieved evidence through a bounded local service using private references."""
from __future__ import annotations

import argparse
import asyncio
import grp
import hashlib
import http.client
import json
import logging
import math
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import SupportsFloat

from openai import APIError, APITimeoutError, AsyncOpenAI
from openai._models import FinalRequestOptions

from .jsontypes import Json, JsonObject, is_json

SOCKET = "/run/redwood/retrieval-score.sock"
REFERENCES = Path("/opt/redwood-probes/references.json")
RELEASE = Path("/etc/redwood-release")
JUDGE_MODEL = "gpt-5.6-sol"
MAX_CHUNKS = 8
CONTEXT_CHARS = 12000
BODY_BYTES = 131072
SCORE_TIMEOUT = 240
CLIENT_TIMEOUT = 270
NAMES = ("context_precision", "context_recall")
_DROPPED = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs", "seed")
COMPLETION_FLOOR = 16384


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_JUDGE_OUTPUT = "invalid_judge_output"
    OVER_BUDGET = "over_budget"
    WRONG_RELEASE = "wrong_release"
    MISSING_CREDENTIALS = "missing_credentials"
    SERVICE_ERROR = "service_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


class ScoreError(Exception):
    """A safe, classified diagnostic failure, never a zero retrieval score."""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.INVALID_REQUEST) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProbeReference:
    reference: str


@dataclass(frozen=True)
class Probe:
    id: str
    question: str
    group: str
    references: Mapping[str, ProbeReference]


@dataclass(frozen=True)
class ScoreRequest:
    query: str
    chunks: list[str]
    api_key: str | None


ScoreFunction = Callable[[str, list[str], str, str], Mapping[str, object]]


def _json_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) and is_json(item) for key, item in value.items()):
        return None
    result: JsonObject = {}
    for key, item in value.items():
        if isinstance(key, str) and is_json(item):
            result[key] = item
    return result


def use_completion_tokens(kwargs: JsonObject) -> JsonObject:
    """Adapt the pinned RAGAS chat arguments to the configured judge."""
    legacy = kwargs.pop("max_tokens", None)
    if legacy is not None:
        kwargs.setdefault("max_completion_tokens", legacy)
    if "max_completion_tokens" in kwargs:
        kwargs["max_completion_tokens"] = max(
            _token_count(kwargs["max_completion_tokens"]),
            COMPLETION_FLOOR,
        )
    for name in _DROPPED:
        kwargs.pop(name, None)
    return kwargs


class JudgeClient(AsyncOpenAI):
    async def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions:
        prepared = options.model_copy()
        body = _json_object(prepared.json_data)
        if prepared.url.endswith("/chat/completions") and body is not None:
            prepared.json_data = use_completion_tokens(body)
        return prepared


def _token_count(value: Json) -> int:
    if not isinstance(value, (int, float, str)):
        raise TypeError(
            "int() argument must be a string, a bytes-like object or a real number, "
            f"not '{type(value).__name__}'"
        )
    return int(value)


def _probe_reference(value: object) -> ProbeReference:
    if not isinstance(value, dict):
        raise ValueError("invalid probe reference")
    reference = value.get("reference")
    if not isinstance(reference, str):
        raise ValueError("invalid probe reference")
    return ProbeReference(reference)


def _probe(value: object) -> Probe:
    if not isinstance(value, dict):
        raise ValueError("invalid probe")
    probe_id, question, group, raw_references = (
        value.get("id"), value.get("question"), value.get("group"), value.get("references")
    )
    if not isinstance(probe_id, str) or not isinstance(question, str) or not isinstance(group, str):
        raise ValueError("invalid probe")
    if not isinstance(raw_references, dict):
        raise ValueError("invalid probe references")
    references = {
        release: _probe_reference(reference)
        for release, reference in raw_references.items()
        if isinstance(release, str)
    }
    return Probe(probe_id, question, group, references)


def load_references(path: Path = REFERENCES) -> dict[str, Probe]:
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or not isinstance(document.get("probes"), list):
        raise ValueError("invalid references document")
    probes = [_probe(row) for row in document["probes"]]
    questions = {probe.question: probe for probe in probes}
    if len(questions) != len(probes):
        raise ValueError("duplicate practice questions")
    return questions


def _number(value: object) -> float:
    if not isinstance(value, (str, bytes, SupportsFloat)):
        error = (
            "float() argument must be a string or a real number, "
            f"not '{type(value).__name__}'"
        )
        raise ScoreError(f"judge returned an invalid score: {error}", ErrorCode.INVALID_JUDGE_OUTPUT)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ScoreError("judge returned an invalid score", ErrorCode.INVALID_JUDGE_OUTPUT) from None
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ScoreError("judge returned an invalid score", ErrorCode.INVALID_JUDGE_OUTPUT)
    return round(number, 4)


async def _ragas_score(query: str, chunks: list[str], reference: str, api_key: str) -> dict[str, float]:
    from ragas.llms import (  # type: ignore[import-not-found]  # noqa: PLC0415 - heavy optional dependency, only the scoring server needs it
        llm_factory,
    )
    from ragas.metrics.collections import (  # type: ignore[import-not-found]  # noqa: PLC0415 - heavy optional dependency, only the scoring server needs it
        ContextPrecision,
        ContextRecall,
    )

    logging.getLogger("instructor").setLevel(logging.CRITICAL)
    async with JudgeClient(api_key=api_key, timeout=60.0, max_retries=1) as client:
        llm = llm_factory(JUDGE_MODEL, client=client)
        metrics = (
            ContextPrecision(llm=llm),
            ContextRecall(llm=llm),
        )
        retrieved_contexts: list[Json] = [*chunks]
        shared: JsonObject = {
            "user_input": query,
            "retrieved_contexts": retrieved_contexts,
            "reference": reference,
        }
        scores: dict[str, float] = {}
        for name, metric in zip(NAMES, metrics, strict=True):
            result = await metric.ascore(**shared)
            scores[name] = _number(result.value)
        return scores


def _classify_failure(error: Exception) -> ScoreError:
    cause = error.__cause__ or error.__context__
    for _ in range(8):
        if isinstance(cause, (TimeoutError, APITimeoutError)):
            return ScoreError("retrieval scoring timed out; no score was produced", ErrorCode.TIMEOUT)
        if isinstance(cause, APIError):
            return ScoreError("judge provider unavailable; no score was produced", ErrorCode.PROVIDER_ERROR)
        if cause is None:
            break
        cause = cause.__cause__ or cause.__context__
    return ScoreError("judge could not produce a valid score", ErrorCode.INVALID_JUDGE_OUTPUT)


def ragas_score(query: str, chunks: list[str], reference: str, api_key: str) -> dict[str, float]:
    if not chunks:
        return dict.fromkeys(NAMES, 0.0)
    try:
        return asyncio.run(asyncio.wait_for(_ragas_score(query, chunks, reference, api_key), SCORE_TIMEOUT))
    except (TimeoutError, APITimeoutError):
        raise ScoreError("retrieval scoring timed out; no score was produced", ErrorCode.TIMEOUT) from None
    except APIError:
        raise ScoreError("judge provider unavailable; no score was produced", ErrorCode.PROVIDER_ERROR) from None
    except ScoreError:
        raise
    except Exception as error:
        raise _classify_failure(error) from None


def _parse_score_request(body: Json, probes: Mapping[str, Probe]) -> ScoreRequest:
    if not isinstance(body, dict):
        raise ScoreError("request must be a JSON object")
    query = _request_query(body.get("query"), probes)
    chunks = _request_chunks(body.get("chunks"))
    _check_chunk_budget(chunks)
    return ScoreRequest(query, chunks, _request_api_key(body.get("api_key")))


def _request_query(value: Json, probes: Mapping[str, Probe]) -> str:
    if not isinstance(value, str) or value not in probes:
        raise ScoreError("unknown query")
    return value


def _request_chunks(value: Json) -> list[str]:
    if not isinstance(value, list):
        raise ScoreError("chunks must be strings")
    chunks: list[str] = []
    for chunk in value:
        if not isinstance(chunk, str):
            raise ScoreError("chunks must be strings")
        chunks.append(chunk)
    return chunks


def _check_chunk_budget(chunks: list[str]) -> None:
    if len(chunks) > MAX_CHUNKS or sum(map(len, chunks)) > CONTEXT_CHARS:
        raise ScoreError(
            f"at most {MAX_CHUNKS} chunks and {CONTEXT_CHARS} total characters",
            ErrorCode.OVER_BUDGET,
        )


def _request_api_key(value: Json) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _score_result(result: Mapping[str, object]) -> JsonObject:
    try:
        scores: JsonObject = {name: _number(result[name]) for name in NAMES}
    except (KeyError, TypeError):
        raise ScoreError("judge returned an invalid result", ErrorCode.INVALID_JUDGE_OUTPUT) from None
    return scores


def evaluate(body: Json, probes: Mapping[str, Probe], score_fn: ScoreFunction, *, release: str) -> JsonObject:
    request = _parse_score_request(body, probes)
    probe = probes[request.query]
    reference = probe.references.get(release)
    if reference is None:
        raise ScoreError("question is not available in this release", ErrorCode.WRONG_RELEASE)
    texts = [chunk for chunk in request.chunks if chunk.strip()]
    if texts and (request.api_key is None or not request.api_key.strip()):
        raise ScoreError("OPENAI_API_KEY is required for nonempty evidence", ErrorCode.MISSING_CREDENTIALS)
    if texts:
        values = score_fn(request.query, texts, reference.reference, request.api_key or "")
    else:
        values = dict.fromkeys(NAMES, 0.0)
    return _score_result(values) | {"release": release, "group": probe.group}


def score(query: str, chunks: list[str], *, socket_path: str = SOCKET) -> JsonObject:
    """Score actual retrieved evidence, including follow-up reads, without truncation."""
    payload = json.dumps(
        {"query": query, "chunks": chunks, "api_key": os.environ.get("OPENAI_API_KEY", "")},
        ensure_ascii=False,
    ).encode()
    if len(payload) > BODY_BYTES:
        raise ScoreError("request body too large", ErrorCode.OVER_BUDGET)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(CLIENT_TIMEOUT)
            sock.connect(socket_path)
            sock.sendall(
                b"POST /score HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            with http.client.HTTPResponse(sock) as response:
                response.begin()
                body = _json_object(json.loads(response.read(BODY_BYTES)))
                if body is None:
                    raise ValueError("invalid response")
                _raise_remote_error(body)
                if response.status != 200:
                    raise ScoreError("score service returned an invalid response", ErrorCode.SERVICE_ERROR)
                for name in NAMES:
                    _number(body[name])
                return body
    except TimeoutError:
        raise ScoreError("score service timed out; no score was produced", ErrorCode.TIMEOUT) from None
    except OSError:
        raise ScoreError("retrieval score service is not available", ErrorCode.SERVICE_UNAVAILABLE) from None
    except (http.client.HTTPException, ValueError, KeyError, TypeError):
        raise ScoreError("score service returned an invalid response", ErrorCode.SERVICE_ERROR) from None


def _raise_remote_error(body: JsonObject) -> None:
    error = body.get("error")
    if not isinstance(error, dict):
        return
    message = error.get("message")
    code_value = error.get("code")
    if isinstance(message, str) and isinstance(code_value, str):
        try:
            code = ErrorCode(code_value)
        except ValueError:
            code = ErrorCode.SERVICE_ERROR
        raise ScoreError(message, code)


@dataclass
class ScoreEvent:
    values: JsonObject = field(default_factory=lambda: {"judge_model": JUDGE_MODEL, "scorer_version": 2})

    def update(self, values: Mapping[str, Json]) -> None:
        self.values.update(values)

    def to_json(self) -> JsonObject:
        return self.values


def _request_length(handler: BaseHTTPRequestHandler) -> int:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise ScoreError("invalid Content-Length") from None
    if not 0 < length <= BODY_BYTES:
        raise ScoreError("invalid request body size", ErrorCode.OVER_BUDGET)
    return length


def _request_body(handler: BaseHTTPRequestHandler) -> Json:
    length = _request_length(handler)
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise ScoreError("incomplete request body")
    try:
        decoded: object = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ScoreError("invalid JSON") from None
    if not is_json(decoded):
        raise ScoreError("invalid JSON")
    return decoded


def _score_server(handler: BaseHTTPRequestHandler) -> _ScoreServer:
    if not isinstance(handler.server, _ScoreServer):
        raise RuntimeError("score handler has an invalid server")
    return handler.server


def _request_metadata(body: Json, probes: Mapping[str, Probe], event: ScoreEvent) -> None:
    if not isinstance(body, dict):
        return
    chunks = body.get("chunks")
    event.update(
        {"evidence_hash": hashlib.sha256(json.dumps(chunks, ensure_ascii=True).encode()).hexdigest()}
    )
    query = body.get("query")
    if not isinstance(query, str):
        return
    probe = probes.get(query)
    if probe is not None:
        event.update({"probe_id": probe.id, "group": probe.group})


def _error_status(error: ScoreError) -> int:
    client_errors = {
        ErrorCode.INVALID_REQUEST,
        ErrorCode.OVER_BUDGET,
        ErrorCode.WRONG_RELEASE,
        ErrorCode.MISSING_CREDENTIALS,
    }
    return 400 if error.code in client_errors else 503


class _ScoreServer(HTTPServer):
    address_family = socket.AF_UNIX

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        probes: dict[str, Probe],
        score_fn: ScoreFunction,
        release_path: Path,
    ) -> None:
        self._socket_path = address[0]
        self.probes = probes
        self.score_fn = score_fn
        self.release_path = release_path
        super().__init__(address, handler)

    def server_bind(self) -> None:
        self.socket.bind(self._socket_path)
        self.server_name = "localhost"
        self.server_port = 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        started = time.monotonic()
        event = ScoreEvent()
        status = 200
        payload: JsonObject = {}
        server = _score_server(self)
        try:
            body, release = self._read_request(server, event)
            payload = evaluate(body, server.probes, server.score_fn, release=release)
            event.update(payload)
            event.update({"status": "ok"})
        except ScoreError as error:
            status, payload = _score_error_response(error)
            event.update({"status": "error", "error_code": error.code})
        except TimeoutError:
            status, payload = 408, {"error": {"code": "timeout", "message": "request timed out"}}
            event.update({"status": "error", "error_code": ErrorCode.TIMEOUT})
        # Fail closed with a generic payload; diagnostics may contain evidence or credentials.
        except Exception:
            status, payload = 503, {
                "error": {"code": "service_error", "message": "score service failed; no score was produced"}
            }
            event.update({"status": "error", "error_code": ErrorCode.SERVICE_ERROR})
        elapsed = round(time.monotonic() - started, 3)
        payload["elapsed_seconds"] = elapsed
        event.update({"elapsed_seconds": elapsed})
        print(json.dumps(event.to_json()), file=sys.stderr, flush=True)
        self._send(status, payload)
        self.close_connection = True

    def _read_request(self, server: _ScoreServer, event: ScoreEvent) -> tuple[Json, str]:
        self.connection.settimeout(10)
        if self.path != "/score":
            raise ScoreError("unknown endpoint")
        body = _request_body(self)
        release = server.release_path.read_text().strip()
        event.update({"release": release})
        _request_metadata(body, server.probes, event)
        return body, release

    def _send(self, status: int, payload: JsonObject) -> None:
        try:
            raw = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _score_error_response(error: ScoreError) -> tuple[int, JsonObject]:
    return _error_status(error), {"error": {"code": error.code, "message": str(error)}}


def open_server(
    path: str = SOCKET,
    references: Path = REFERENCES,
    *,
    grant_harbor: bool = True,
    release_path: Path = RELEASE,
    score_fn: ScoreFunction = ragas_score,
) -> HTTPServer:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).unlink(missing_ok=True)
    probes = load_references(references)
    server = _ScoreServer((path, 0), _Handler, probes, score_fn, release_path)
    if grant_harbor:
        Path(path).parent.chmod(0o755)
        os.chown(path, 0, grp.getgrnam("harbor").gr_gid)
        os.chmod(path, 0o660)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        with open_server() as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
