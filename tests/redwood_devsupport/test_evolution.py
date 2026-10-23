import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tasks" / "redwood-devsupport" / "tests"
sys.path.insert(0, str(TESTS))

sys.path.insert(0, str(TESTS.parent / "environment"))

from evolve import diagnostics  # noqa: E402
from grade.grade import CaseRecord  # noqa: E402
from grade.scenarios import Scenario  # noqa: E402
from run.run_agent import ModelCall, Result  # noqa: E402


def test_drift_counts_only_cases_a1_passes_and_a0_failed():
    result = diagnostics.drift({"a": False, "b": True, "c": False}, {"a": True, "b": True, "c": False},
                             {"a": ["R1"], "b": ["R1"], "c": ["R2"]})
    assert result.absorbed == ["a"]
    assert result.already_passed_by_a0 == ["b"]
    assert result.absorbed_by_rule == {"R1": "1/2", "R2": "0/1"}


def test_regression_lists_cases_a0_passed_and_a1_fails():
    result = diagnostics.regression({"a": True, "b": False}, {"a": False, "b": True})
    assert result.regressed == ["a"]
    assert result.improved == ["b"]


def test_layer_sizes_and_health(tmp_path):
    (tmp_path / "skills" / "x").mkdir(parents=True)
    (tmp_path / "skills" / "x" / "SKILL.md").write_text("a\nb\n")
    (tmp_path / "tools.py").write_text("t\n")
    (tmp_path / "index.lance").write_bytes(b"12345")
    sizes = diagnostics.layer_sizes(tmp_path)
    assert sizes["skills_lines"] == 2 and sizes["tools_lines"] == 1 and sizes["other_bytes"] == 5
    cases = tmp_path / "log" / "cases"
    cases.mkdir(parents=True)
    call = ModelCall(model="claude-sonnet-5-5", kwargs={}, instructions_chars=0, tools=0, prompt_tokens=10, completion_tokens=2,
                     messages=[], response={})
    result = Result(case_id="a", seconds=4.0, messages=[{"role": "user"}, {"role": "assistant"}], model_calls=[call])
    (cases / "a.json").write_text(CaseRecord(scenario=Scenario(id="a"), result=result).model_dump_json())
    run = diagnostics.run_health(tmp_path / "log")
    assert run.cases == 1 and run.prompt_tokens == 10 and run.customer_turns == 1
    assert run.passing is None


def test_a_case_missing_from_either_run_is_unmeasured_not_compared():
    result = diagnostics.drift({"a": False}, {"a": True, "b": True}, {"a": ["R1"], "b": ["R1"]})
    assert result.absorbed == ["a"] and result.unmeasured == ["b"]
    assert diagnostics.regression({"a": True, "b": True}, {"b": False}).unmeasured == ["a"]


def test_a_failed_part_keeps_the_other_diagnostics(tmp_path, monkeypatch):
    def broken(workspace):
        raise RuntimeError("workspace unreadable")

    monkeypatch.setattr(diagnostics, "layer_sizes", broken)
    (tmp_path / "a0").mkdir()
    (tmp_path / "a1").mkdir()
    runs = {"q00": {"x": True}, "q01": None, "q10": {"x": True}, "q11": {"y": True}}
    evolution = diagnostics.report(tmp_path / "a0", tmp_path / "a1", runs, {}, {"y": ("R1",)})
    assert evolution.sizes is None and evolution.drift is None
    assert evolution.regression is not None and evolution.regression.regressed == []
    assert evolution.errors == {"sizes": "RuntimeError: workspace unreadable"}
