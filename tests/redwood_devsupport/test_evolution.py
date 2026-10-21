import json
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


def _workspaces(tmp_path, before, after):
    old, new = tmp_path / "old", tmp_path / "new"
    for root, files in ((old, before), (new, after)):
        root.mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text) if isinstance(text, str) else (root / name).write_bytes(text)
    return old, new


def test_the_diff_covers_every_changed_file_and_lists_lines_added_to_markdown(tmp_path):
    old, new = _workspaces(tmp_path, {"AGENTS.md": "keep\nold rule\n", "index.bin": b"\xff\x00"},
                           {"AGENTS.md": "keep\nnew rule\n", "index.bin": b"\xff\x01", "skills/a/SKILL.md": "added\n"})
    diffs = diagnostics.file_diffs(old, new)
    assert set(diffs) == {"AGENTS.md", "index.bin", "skills/a/SKILL.md"}
    assert diagnostics.added_markdown(diffs) == ["new rule", "added"]


def test_an_empty_diff_is_no_change_not_a_score(tmp_path):
    old, new = _workspaces(tmp_path, {"AGENTS.md": "same\n"}, {"AGENTS.md": "same\n"})
    result = diagnostics.placement(old, new, {"BILL-CREDIT"})
    assert result.no_change and result.placed == "0/0"


def test_a_change_a1_did_not_absorb_is_not_scored(tmp_path):
    old, new = _workspaces(tmp_path, {"tools.py": "LIMIT = 500\n"}, {"tools.py": "LIMIT = 250\n"})
    result = diagnostics.placement(old, new, {"BILL-CREDIT"})
    status = {c.id: c.status for c in result.changes}
    assert status == {"BILL-CREDIT": diagnostics.Status.PLACED, "R1-FACTS": diagnostics.Status.NOT_ABSORBED}
    assert result.placed == "1/1"  # only changes with a scan are reported


def test_a_scan_finds_a_fact_written_into_markdown(tmp_path):
    old, new = _workspaces(tmp_path, {"AGENTS.md": "x\n"}, {"AGENTS.md": "x\nCredit up to $250 per line.\n"})
    result = diagnostics.placement(old, new, {"BILL-CREDIT"})
    credit = next(c for c in result.changes if c.id == "BILL-CREDIT")
    [prose] = [c for c in credit.checks if c.kind == "scan"]
    assert prose.status is diagnostics.Status.MISPLACED and "$250" in prose.quote


def test_every_grounds_quote_is_in_the_builder_visible_file():
    environment = TESTS.parent / "environment"
    roots = {"app": environment / "app", "R1": TESTS.parents[2] / "tasks" / "redwood-devsupport" / "environment" / "corpus" / "R1"}
    for change in diagnostics.load_delta().changes:
        if change.grounds is None:
            continue
        root, _, rest = change.grounds.file.partition("/")
        path = roots[root] / rest
        assert " ".join(change.grounds.quote.split()) in " ".join(_strings(path).split()), change.id


def _strings(path):
    if path.suffix != ".json":
        return path.read_text()
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(path.read_text()))
    return "\n".join(found)


def test_layer_sizes_and_health(tmp_path):
    (tmp_path / "skills" / "x").mkdir(parents=True)
    (tmp_path / "skills" / "x" / "SKILL.md").write_text("a\nb\n")
    (tmp_path / "tools.py").write_text("t\n")
    (tmp_path / "index.lance").write_bytes(b"12345")
    sizes = diagnostics.layer_sizes(tmp_path)
    assert sizes["skills_lines"] == 2 and sizes["tools_lines"] == 1 and sizes["other_bytes"] == 5
    cases = tmp_path / "log" / "cases"
    cases.mkdir(parents=True)
    call = ModelCall(model="claude-opus-5-5", kwargs={}, instructions_chars=0, tools=0, prompt_tokens=10, completion_tokens=2,
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
    def broken(old, new, handled):
        raise RuntimeError("diff unreadable")

    monkeypatch.setattr(diagnostics, "placement", broken)
    (tmp_path / "a0").mkdir()
    (tmp_path / "a1").mkdir()
    runs = {"q00": {"x": True}, "q01": None, "q10": {"x": True}, "q11": {"y": True}}
    evolution = diagnostics.report(tmp_path / "a0", tmp_path / "a1", runs, {}, {"y": ("R1",)})
    assert evolution.placement is None and evolution.drift is None
    assert evolution.regression is not None and evolution.regression.regressed == []
    assert evolution.sizes is not None
    assert evolution.errors == {"placement": "RuntimeError: diff unreadable"}
