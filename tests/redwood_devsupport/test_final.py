"""The run's reward gate and scores.json, from graded case records. No API calls."""
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport" / "tests"
sys.path[:0] = [str(TESTS), str(TESTS.parent / "environment")]

from grade import final  # noqa: E402
from grade.grade import CaseRecord, ScenarioGrade  # noqa: E402
from grade.scenarios import Scenario  # noqa: E402
from run.run_agent import Result, Termination  # noqa: E402


@pytest.mark.parametrize(("q00", "q10", "q11", "reward"), [
    (1.0, 1.0, 0.9, 1.0),  # built well, maintained, kept R0
    (0.9, 0.8, 1.0, 1.0),  # one R0 case of slack after maintenance
    (0.9, 0.7, 1.0, 0.0),  # maintenance broke R0
    (1.0, 1.0, 0.5, 0.5),  # built well, did not absorb R1
    (0.8, 0.8, 0.9, 0.0),  # weak build (today's Opus run)
])
def test_gate(q00, q10, q11, reward):
    assert final.gate(q00, q10, q11) == reward


def _run(out, name, passed):
    cases = out / name / "cases"
    cases.mkdir(parents=True)
    for case_id, ok in passed.items():
        grade = ScenarioGrade(id=case_id, termination=Termination.CUSTOMER_STOP, passed=ok, checks=[])
        record = CaseRecord(scenario=Scenario(id=case_id), result=Result(case_id=case_id), grade=grade)
        (cases / f"{case_id}.json").write_text(record.model_dump_json())


def test_score_writes_the_gate_and_the_diagnostics(tmp_path):
    out, a0, a1 = tmp_path / "out", tmp_path / "a0", tmp_path / "a1"
    a0.mkdir()
    a1.mkdir()
    _run(out, "q00", {"a0-1": True, "a0-2": True})
    _run(out, "q01", {"a1-1": False})
    _run(out, "q10", {"a0-1": True, "a0-2": True})
    _run(out, "q11", {"a1-1": True})
    assert final.main([str(out), str(a0), str(a1)]) == 0
    assert json.loads((out / "reward.json").read_text()) == {"reward": 1.0}
    scores = json.loads((out / "scores.json").read_text())
    assert scores["q"] == {"q00": 1.0, "q01": 0.0, "q10": 1.0, "q11": 1.0}
    assert scores["drift"]["absorbed"] == ["a1-1"] and scores["regression"]["regressed"] == []


def test_score_refuses_a_run_missing_a_gated_q(tmp_path):
    _run(tmp_path, "q00", {"a0-1": True})
    with pytest.raises(SystemExit, match="q10, q11 not graded"):
        final.score(tmp_path, tmp_path, tmp_path)
