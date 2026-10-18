"""The maintain_r1 diagnostics report. None of it is part of the reward.

    axis 1  placement    did each R1 change A1 absorbed land where the builder docs say it belongs?
    axis 2  health       per-case means of seconds, model calls, tokens, embedding calls and
                         customer turns; lines per workspace layer and bytes of everything else.
    axis 3  drift        Q01 (A0 on R1 cases) vs Q11 (A1 on R1 cases): cases A1 absorbed.
            regression   Q00 (A0 on retained R0 cases) vs Q10 (A1 on them, R1 console): cases lost.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

from grade.grade import CaseRecord, credits
from pydantic import BaseModel
from run.run_agent import Result

DATA = Path(__file__).resolve().parents[1] / "data"

# ---- axis 1: did each R1 change land where the builder docs say it belongs?
#
#
# Reads the A0 -> A1 workspace diff and checks each change in delta_r1.json with its scans:
# regexes over the lines the diff adds to markdown files (e.g. a limit written as prose). No judge.
# A change is checked only when A1 handles it (passes a case of one of its rules), so an A1 that
# ignored a change is `not_absorbed` rather than credited for what it didn't add. An empty diff is
# `no_change`. Never part of the reward.


# ---- delta_r1.json

class Grounds(BaseModel):
    """The builder-visible line that sets the expectation: `file` is under app/ or R1/."""

    file: str
    quote: str


class Scan(BaseModel):
    about: str
    pattern: str
    expect: bool


class Change(BaseModel):
    id: str
    rules: list[str]
    grounds: Grounds | None
    scans: list[Scan] = []

    def applies(self, handled: set[str]) -> bool:
        return not self.rules or bool(handled.intersection(self.rules))


class Delta(BaseModel):
    about: str
    changes: list[Change]


def load_delta() -> Delta:
    return Delta.model_validate_json((DATA / "delta_r1.json").read_text())


# ---- results

class Status(StrEnum):
    PLACED = "placed"
    MISPLACED = "misplaced"
    NOT_ABSORBED = "not_absorbed"


class Verdict(BaseModel):
    """Whether a scan matched, and the first matching line."""

    yes: bool
    quote: str = ""

    def against(self, expect: bool) -> Status:
        return Status.PLACED if self.yes == expect else Status.MISPLACED


class Check(BaseModel):
    kind: str  # "scan"
    check: str
    expect: bool
    status: Status
    quote: str = ""


class ChangeResult(BaseModel):
    id: str
    counted: bool  # False when the change has no grounds: a reference-agent comparison only
    status: Status
    checks: list[Check] = []


class Placement(BaseModel):
    no_change: bool
    placed: str  # "placed/checked" over the counted changes A1 absorbed
    changes: list[ChangeResult] = []


# ---- the diff

def _names(workspace: Path) -> set[str]:
    return {p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return path.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:
        return ["<binary>\n"]


def file_diffs(old: Path, new: Path) -> dict[str, str]:
    """Changed file -> its unified diff (binary files: one marker line each side)."""
    diffs = {}
    for name in sorted(_names(old) | _names(new)):
        before, after = old / name, new / name
        if before.is_file() and after.is_file() and before.read_bytes() == after.read_bytes():
            continue
        diffs[name] = "".join(difflib.unified_diff(_lines(before), _lines(after), f"a/{name}", f"b/{name}"))
    return diffs


def added_markdown(diffs: dict[str, str]) -> list[str]:
    return [line[1:] for name, diff in diffs.items() if name.endswith(".md")
            for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


# ---- checking

def scan(pattern: str, lines: list[str]) -> Verdict:
    hits = [line for line in lines if re.search(pattern, line, re.IGNORECASE)]
    return Verdict(yes=bool(hits), quote=hits[0].strip() if hits else "")


def overall(statuses: Iterable[Status]) -> Status:
    """Placed only when every check is."""
    return Status.MISPLACED if Status.MISPLACED in set(statuses) else Status.PLACED


def placement(old: Path, new: Path, handled: set[str]) -> Placement:
    """`handled`: the rules A1 passes at least one scenario of, which decide which changes are checked.

    Only changes with scans are reported; a change without one has nothing code can check.
    """
    diffs = file_diffs(old, new)
    if not diffs:
        return Placement(no_change=True, placed="0/0")
    changes = [change for change in load_delta().changes if change.scans]
    added = added_markdown(diffs)
    results = [_result(change, added) if change.applies(handled) else _not_absorbed(change)
               for change in changes]
    return Placement(no_change=False, placed=_placed(results), changes=results)


def _not_absorbed(change: Change) -> ChangeResult:
    return ChangeResult(id=change.id, counted=change.grounds is not None, status=Status.NOT_ABSORBED)


def _placed(results: list[ChangeResult]) -> str:
    """"placed/checked" over the grounded changes A1 absorbed."""
    scored = [r for r in results if r.counted and r.status is not Status.NOT_ABSORBED]
    return f"{sum(r.status is Status.PLACED for r in scored)}/{len(scored)}"


def _result(change: Change, added: list[str]) -> ChangeResult:
    checks = [_check("scan", s.about, s.expect, scan(s.pattern, added)) for s in change.scans]
    return ChangeResult(id=change.id, counted=change.grounds is not None,
                        status=overall(c.status for c in checks), checks=checks)


def _check(kind: str, check: str, expect: bool, verdict: Verdict) -> Check:
    return Check(kind=kind, check=check, expect=expect, status=verdict.against(expect), quote=verdict.quote)


# ---- axis 3: drift and regression

Outcomes = dict[str, bool]
"""Scenario id -> passed, for one run."""


class CaseChange(BaseModel):
    id: str
    before: bool
    after: bool

    @property
    def gained(self) -> bool:
        return self.after and not self.before

    @property
    def lost(self) -> bool:
        return self.before and not self.after


class Drift(BaseModel):
    absorbed: list[str]
    unmeasured: list[str]
    already_passed_by_a0: list[str]
    absorbed_by_rule: dict[str, str]
    cases: list[CaseChange]


class Regression(BaseModel):
    regressed: list[str]
    unmeasured: list[str]
    improved: list[str]
    cases: list[CaseChange]


def case_changes(before: Outcomes, after: Outcomes) -> list[CaseChange]:
    """Cases graded in both runs; a case one run dropped as invalid is not compared."""
    return [CaseChange(id=case_id, before=before[case_id], after=after[case_id])
            for case_id in sorted(before.keys() & after.keys())]


def unmeasured(before: Outcomes, after: Outcomes) -> list[str]:
    return sorted(before.keys() ^ after.keys())


def drift(q01: Outcomes, q11: Outcomes, rules: dict[str, tuple[str, ...]]) -> Drift:
    changes = case_changes(q01, q11)
    return Drift(absorbed=[c.id for c in changes if c.gained], unmeasured=unmeasured(q01, q11),
                 already_passed_by_a0=[c.id for c in changes if c.before],
                 absorbed_by_rule=_absorbed_by_rule(changes, rules), cases=changes)


def _absorbed_by_rule(changes: list[CaseChange], rules: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """"absorbed/covering" per R1 rule, over the cases that exercise it."""
    by_rule: dict[str, str] = {}
    for rule in sorted({rule for case_rules in rules.values() for rule in case_rules}):
        covering = [c for c in changes if rule in rules.get(c.id, ())]
        by_rule[rule] = f"{sum(c.gained for c in covering)}/{len(covering)}"
    return by_rule


def regression(q00: Outcomes, q10: Outcomes) -> Regression:
    changes = case_changes(q00, q10)
    return Regression(regressed=[c.id for c in changes if c.lost], improved=[c.id for c in changes if c.gained],
                      unmeasured=unmeasured(q00, q10), cases=changes)


# ---- axis 2: health

LAYERS = {
    "system_prompt": ("system_prompt.md",),
    "agents": ("AGENTS.md",),
    "skills": ("skills/**/*.md",),
    "subagents": ("subagents/*.md",),
    "hooks": ("hooks.py",),
    "tools": ("tools.py", "billing_rules.py"),
}


class CaseCost(BaseModel):
    """What one conversation cost."""

    seconds: float
    model_calls: float
    prompt_tokens: float
    completion_tokens: float
    embedding_calls: float
    customer_turns: float
    credits: float

    @classmethod
    def of(cls, result: Result) -> CaseCost:
        return cls(
            seconds=result.seconds,
            model_calls=len(result.model_calls),
            prompt_tokens=sum(call.prompt_tokens for call in result.model_calls),
            completion_tokens=sum(call.completion_tokens for call in result.model_calls),
            embedding_calls=len(result.embedding_calls),
            customer_turns=sum(message.get("role") == "user" for message in result.messages),
            credits=credits(result.model_calls),
        )


class RunHealth(CaseCost):
    """Per-case means of `CaseCost` over one run, and over only its passing cases.

    Compare runs on `passing`: an agent that fails early looks cheap over all cases.
    """

    cases: int
    passing: CaseCost | None


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def run_health(log_dir: Path) -> RunHealth:
    """Per-case means over the case records `grade.run_step` wrote to `log_dir/cases`."""
    records = [CaseRecord.model_validate_json(path.read_text())
               for path in sorted((log_dir / "cases").glob("*.json"))]
    passing = [r.result for r in records if r.grade is not None and r.grade.passed]
    return RunHealth(cases=len(records), passing=_means(passing) if passing else None,
                     **_means([r.result for r in records]).model_dump())


def _means(results: list[Result]) -> CaseCost:
    costs = [CaseCost.of(result).model_dump() for result in results]
    return CaseCost.model_validate({field: _mean([cost[field] for cost in costs]) for field in CaseCost.model_fields})


def _files(workspace: Path) -> list[Path]:
    return [p for p in workspace.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def layer_sizes(workspace: Path) -> dict[str, int]:
    """Lines per layer, and the bytes of everything else (mostly the retrieval index)."""
    sizes: dict[str, int] = {}
    counted: set[Path] = set()
    for layer, patterns in LAYERS.items():
        files = {path for pattern in patterns for path in workspace.glob(pattern) if path.is_file()}
        counted |= files
        sizes[f"{layer}_lines"] = sum(len(path.read_text().splitlines()) for path in files)
    sizes["other_bytes"] = sum(path.stat().st_size for path in _files(workspace) if path not in counted)
    return sizes


# ---- the report

class EvolutionReport(BaseModel):
    """Each part is None when it could not be computed; `errors` says why, keyed by part."""

    drift: Drift | None = None
    regression: Regression | None = None
    health: dict[str, RunHealth] | None = None
    sizes: dict[str, dict[str, int]] | None = None
    placement: Placement | None = None
    errors: dict[str, str] = {}


def attempt[T](errors: dict[str, str], name: str, compute: Callable[[], T]) -> T | None:
    """`compute()`, or None with the failure recorded under `name`, so one part never loses the others."""
    try:
        return compute()
    except Exception as error:
        errors[name] = f"{type(error).__name__}: {error}"
        return None


def handled_rules(q11: Outcomes | None, rules: dict[str, tuple[str, ...]]) -> set[str]:
    """Rules A1 passes at least one R1 scenario of; with no Q11, every rule, so placement checks everything."""
    if q11 is None:
        return {rule for case_rules in rules.values() for rule in case_rules}
    return {rule for case_id, passed in q11.items() if passed for rule in rules.get(case_id, ())}


def report(a0: Path, a1: Path, runs: dict[str, Outcomes | None], logs: dict[str, Path],
           rules: dict[str, tuple[str, ...]]) -> EvolutionReport:
    """The diagnostics from whichever runs completed (a failed run is None)."""
    errors: dict[str, str] = {}
    q00, q01, q10, q11 = runs["q00"], runs["q01"], runs["q10"], runs["q11"]
    return EvolutionReport(
        drift=attempt(errors, "drift", lambda: drift(q01, q11, rules)) if q01 is not None and q11 is not None else None,
        regression=attempt(errors, "regression", lambda: regression(q00, q10))
        if q00 is not None and q10 is not None else None,
        health=attempt(errors, "health", lambda: {name: run_health(path) for name, path in logs.items()
                                                  if (path / "cases").is_dir()}),
        sizes=attempt(errors, "sizes", lambda: {"a0": layer_sizes(a0), "a1": layer_sizes(a1)}),
        placement=attempt(errors, "placement", lambda: placement(a0, a1, handled_rules(q11, rules))),
        errors=errors,
    )
