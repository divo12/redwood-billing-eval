"""Release-aware diagnostic contract, provenance, deadlines, and socket recovery; no live API calls."""
import asyncio
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError

TASK = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport"
sys.path[:0] = [str(TASK / "environment")]
from redwood_kit import retrieval_score as rs  # noqa: E402

PROBES = TASK / "environment/r1/framework/retrieval_probes.json"
REFERENCES = TASK / "environment/probe_key/references.json"
CORPUS = TASK / "environment" / "corpus"


def test_every_reference_has_available_verbatim_evidence_and_matching_public_metadata():
    visible = json.loads(PROBES.read_text())["probes"]
    hidden = json.loads(REFERENCES.read_text())["probes"]
    assert len({r["question"] for r in hidden}) == len(hidden)
    assert [r["question"] for r in visible] == [r["question"] for r in hidden]
    assert {r["group"] for r in visible} == {"billing", "general_retrieval"}
    assert any(r["releases"] == ["R1"] for r in visible)
    for public, private in zip(visible, hidden):
        assert public["releases"] == list(private["references"])
        assert (public["id"], public["group"]) == (private["id"], private["group"])
        assert "reference" not in public and "sources" not in public
        for release, ref in private["references"].items():
            assert ref["reference"] == " ".join(ref["required_claims"])
            assert ref["sources"]
            for source in ref["sources"]:
                assert source["source_release"] <= release
                record = json.loads((CORPUS / source["source_release"] / source["path"]).read_text())
                assert source["status"] == record.get("status", "correspondence")
                assert source["scope"] and source["record_date"] and source["evidence"]
                for evidence in source["evidence"]:
                    value = record
                    for key in evidence["field"].split("."):
                        value = value[int(key)] if isinstance(value, list) else value[key]
                    assert evidence["quote"] and evidence["quote"] in value


@contextmanager
def service(score_fn):
    # Unix socket paths are short on macOS as well as Linux.
    with tempfile.TemporaryDirectory(prefix="rw-", dir="/tmp") as directory:
        root = Path(directory)
        release = root / "release"
        release.write_text("R0")
        references = root / "references.json"
        references.write_text(json.dumps({"probes": [
            {"id": "p", "question": "q", "group": "billing", "references": {
                "R0": {"reference": "private R0 truth"}, "R1": {"reference": "private R1 truth"}}},
            {"id": "new", "question": "new q", "group": "billing", "references": {
                "R1": {"reference": "new private truth"}}},
        ]}))
        path = str(root / "score.sock")
        server = rs.open_server(path, references, grant_harbor=False, release_path=release, score_fn=score_fn)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield path, release
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def test_socket_preserves_followup_evidence_switches_release_and_hides_truth(monkeypatch, capsys):
    seen = []

    def fake(query, chunks, reference, api_key):
        seen.append((chunks, reference, api_key))
        return {"context_precision": 0.6, "context_recall": 0.9}

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-credential")
    evidence = [" first hit ", "x" * 1600 + " crucial fact from follow-up read"]
    with service(fake) as (path, release):
        first = rs.score("q", evidence, socket_path=path)
        assert seen[-1] == (evidence, "private R0 truth", "sk-fake-credential")
        assert first["release"] == "R0" and first["group"] == "billing"
        assert first["elapsed_seconds"] >= 0
        with pytest.raises(rs.ScoreError) as caught:
            rs.score("new q", evidence, socket_path=path)
        assert caught.value.code == "wrong_release"
        release.write_text("R1")
        second = rs.score("q", evidence, socket_path=path)
        assert seen[-1][1] == "private R1 truth" and second["release"] == "R1"
        assert rs.score("new q", evidence, socket_path=path)["context_recall"] == 0.9
    output = capsys.readouterr().err + json.dumps(first) + str(caught.value)
    assert '"elapsed_seconds"' in output and '"evidence_hash"' in output
    for secret in ("private R0 truth", "private R1 truth", "sk-fake-credential", "crucial fact"):
        assert secret not in output


def test_bad_inputs_and_empty_evidence_do_not_call_judge(monkeypatch):
    def forbidden(*args):
        pytest.fail("invalid or empty evidence called the judge")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with service(forbidden) as (path, _):
        for query, chunks, code in [("unknown", ["x"], "invalid_request"),
                                     ("q", "not a list", "invalid_request"),
                                     ("q", ["x"] * 9, "over_budget"),
                                     ("q", ["x" * 12001], "over_budget"),
                                     ("q", ["x"], "missing_credentials")]:
            with pytest.raises(rs.ScoreError) as caught:
                rs.score(query, chunks, socket_path=path)
            assert caught.value.code == code
        result = rs.score("q", ["  "], socket_path=path)
        assert result["context_precision"] == result["context_recall"] == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, None])
def test_invalid_judge_numbers_are_errors_not_scores(value, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with service(lambda *_: {"context_precision": value, "context_recall": 1}) as (path, _):
        with pytest.raises(rs.ScoreError) as caught:
            rs.score("q", ["x"], socket_path=path)
        assert caught.value.code == "invalid_judge_output"


def test_whole_score_deadline_cancels_work_and_next_request_recovers(monkeypatch):
    calls, cancelled = [], []

    async def slow_then_good(*args):
        calls.append(args)
        if len(calls) == 1:
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.append(True)
        return dict.fromkeys(rs.NAMES, 1.0)

    monkeypatch.setattr(rs, "_ragas_score", slow_then_good)
    monkeypatch.setattr(rs, "SCORE_TIMEOUT", 0.02)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with service(rs.ragas_score) as (path, _):
        with pytest.raises(rs.ScoreError) as caught:
            rs.score("q", ["x"], socket_path=path)
        assert caught.value.code == "timeout" and cancelled == [True]
        assert rs.score("q", ["x"], socket_path=path)["context_recall"] == 1


def test_provider_error_is_classified_without_disclosing_provider_message(monkeypatch):
    async def fail(*args):
        try:
            raise APIConnectionError(message="private provider detail", request=httpx.Request("POST", "https://example.invalid"))
        except APIConnectionError as error:
            raise RuntimeError("structured-output retry wrapper") from error

    monkeypatch.setattr(rs, "_ragas_score", fail)
    with pytest.raises(rs.ScoreError) as caught:
        rs.ragas_score("q", ["x"], "private reference", "sk-fake")
    assert caught.value.code == "provider_error"
    assert "private" not in str(caught.value)


def test_chat_adapter_needs_no_real_credentials():
    kwargs = rs.use_completion_tokens({"max_tokens": 16, "temperature": 0.01, "top_p": 0.9})
    assert kwargs == {"max_completion_tokens": 16384}
    client = rs.JudgeClient(api_key="sk-fake")
    asyncio.run(client.close())


def test_pinned_ragas_uses_real_sdk_adapter_without_live_requests(monkeypatch):
    calls = []

    async def send(client, request, **kwargs):
        body = json.loads(request.content)
        calls.append(body)
        verdict = ({"classifications": [{"statement": "A fact", "reason": "fixture", "attributed": 1}]}
                   if "classifications" in json.dumps(body["messages"]) else {"reason": "fixture", "verdict": 1})
        return httpx.Response(200, request=request, json={
            "id": "fixture", "object": "chat.completion", "created": 0, "model": rs.JUDGE_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(verdict)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    assert rs.ragas_score("q", ["A fact"] * 8, "A fact", "sk-fake") == dict.fromkeys(rs.NAMES, 1.0)
    assert len(calls) == 9
    assert all("max_tokens" not in c and c["max_completion_tokens"] >= 16384 for c in calls)
