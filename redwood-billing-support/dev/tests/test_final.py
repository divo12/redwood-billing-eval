"""The run's reward gate and scores.json, from graded case records. No API calls."""
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[2] / "tests"
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
    assert json.loads((out / "reward.json").read_text()) == {"reward": 1.0, "q00": 1.0, "q10": 1.0, "q11": 1.0}
    scores = json.loads((out / "scores.json").read_text())
    assert scores["q"] == {"q00": 1.0, "q01": 0.0, "q10": 1.0, "q11": 1.0}
    assert scores["drift"]["absorbed"] == ["a1-1"] and scores["regression"]["regressed"] == []


def test_score_refuses_a_run_missing_a_gated_q(tmp_path):
    _run(tmp_path, "q00", {"a0-1": True})
    with pytest.raises(SystemExit, match="q10, q11 not graded"):
        final.score(tmp_path, tmp_path, tmp_path)


def _record(cases, case_id, passed, invalid=None):
    grade = ScenarioGrade(id=case_id, termination=Termination.CUSTOMER_STOP, passed=passed, checks=[])
    result = Result(case_id=case_id, invalid=invalid)
    (cases / f"{case_id}.json").write_text(CaseRecord(scenario=Scenario(id=case_id), result=result,
                                                      grade=grade).model_dump_json())


def test_an_invalid_case_is_listed_and_left_out_of_the_rate(tmp_path):
    cases = tmp_path / "q00" / "cases"
    cases.mkdir(parents=True)
    _record(cases, "a0-01", True)
    _record(cases, "a0-02", False, invalid="the model provider failed: 429")
    assert final.outcomes(tmp_path / "q00") == {"a0-01": True}
    assert final.invalid(tmp_path / "q00") == [{"case": "a0-02", "why": "the model provider failed: 429"}]


def test_twins_count_only_when_both_halves_pass_and_rates_split_by_info_type():
    run = {"a0-03": True, "a0-03-T": False, "a0-04": True, "a0-04-T": True, "a0-02": True}
    summary = final.info({"q00": run, "q01": None})
    assert set(summary) == {"q00"}
    assert summary["q00"]["twins"] == "1/2" and summary["q00"]["one_half"] == ["a0-03/a0-03-T"]
    types = summary["q00"]["by_type"]
    assert sum(int(v.split("/")[1]) for v in types.values()) == 5  # every rated case counted once


def test_robustness_compares_each_variant_with_its_base():
    run = {"a0-07": True, "a0-07-M": True, "a0-07-A": False, "a0-07-C": True,
           "a0-10": False, "a0-10-M": False, "a0-10-A": True, "a0-10-C": False}
    summary = final.robustness({"q00": run, "q01": None})["q00"]
    assert summary["bases"] == "1/2"
    assert (summary["missing_info"], summary["ambiguous"], summary["conflict"]) == ("1/2", "1/2", "1/2")
    assert summary["robustness"] == 1.0  # variants pass at the same rate as their bases
    assert summary["attribution"] == {"behaviour failure": ["a0-07-A"], "handled": ["a0-07-C", "a0-07-M"],
                                      "both failed": ["a0-10-C", "a0-10-M"], "variant only": ["a0-10-A"]}
    assert final.robustness({"q01": {"a1-04": True}}) == {}  # base cases only: nothing to report
