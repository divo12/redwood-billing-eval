"""Grade simulated billing conversations against the hidden scenario checks.

    python -m grade.grade <step> <workspace> [<overlay> ...] [--only ID,ID] [--workers N] [--out DIR]

Each scenario is one conversation (`run_agent.run_case`), graded by judge
questions about the transcript (`judge.py`) and these code-decided checks:

    db      the billing end state equals the gold actions (or an accepted
            alternative) replayed on a fresh seed, compared on the fields that matter.
    action  every expected console operation was matched by a successful call
            (hyper-tau's ACTION, on console operations): the account lookup, the
            handoff facts the receiving team sees, and text no write may store.
    communicate  every expected value appears in some agent message (hyper-tau's
            COMMUNICATE): any one listed form of each value, ignoring case, commas and "$".

A scenario passes when every scored check passes; a conversation that never
finished fails without being judged. The step reward averages the scenarios.
Simulator and judge failures are retried, then raised: they make the run
invalid, never a score.

The command line layers workspace directories (later files win) and grades
them locally, outside Harbor, for calibration. It needs OPENAI_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path
from statistics import mean

TESTS = Path(__file__).resolve().parents[1]
if not Path("/opt/redwood_kit").is_dir():  # a local run from the repository
    sys.path.insert(0, str(TESTS.parent / "environment"))

from grade.judge import CONVERSATION_INSTRUCTIONS, Answer, Ask, JudgeError, ask_judge, normalized, render  # noqa: E402
from grade.scenarios import (  # noqa: E402
    Action,
    DbExpectation,
    OperationKind,
    Release,
    Scenario,
    Step,
    load_scenarios,
)
from pydantic import BaseModel, ConfigDict  # noqa: E402
from redwood_kit.billing import store as billing_store  # noqa: E402
from redwood_kit.billing.store import ConsoleCall  # noqa: E402
from redwood_kit.jsontypes import JsonObject  # noqa: E402
from redwood_kit.messages import AssistantMessage, Message, from_dict  # noqa: E402
from run import run_agent  # noqa: E402
from run.customer import Customer, CustomerError  # noqa: E402
from run.run_agent import (  # noqa: E402
    BillingRecord,
    ModelCall,
    Result,
    Termination,
    load_db,
    run_case,
)

# ---- credits: the agent's cost penalty, modelled on hyper-tau's credit performance requirement
#
#     credits  one conversation: the agent's model-gateway calls, prompt and completion tokens
#              (reasoning counts as completion) priced at the frozen per-million rates
#     overage  max(0, mean credits per conversation / budget - 1), over every graded conversation,
#              failed ones included; 0 while no budget is set
# The step reward is max(0, scenario score - overage). Customer-simulator, judge and embedding
# calls are not metered. Rates and budget mirror environment/app/framework/deployment_manifest.json.

class Rate(BaseModel):
    input_per_million: float
    output_per_million: float


CREDIT_RATES = {"gpt-6-sol": Rate(input_per_million=3.0, output_per_million=15.0)}
# Unset until an oracle run on R0 and R1 calibrates it; no penalty applies until then.
CREDIT_BUDGET: float | None = None


def credits(calls: list[ModelCall]) -> float:
    total = 0.0
    for call in calls:
        rate = CREDIT_RATES[call.model]
        total += call.prompt_tokens * rate.input_per_million + call.completion_tokens * rate.output_per_million
    return total / 1_000_000


class CreditUse(BaseModel):
    mean_credits: float | None
    budget: float | None
    overage: float


def credit_use(per_case: list[float], budget: float | None = CREDIT_BUDGET) -> CreditUse:
    observed = sum(per_case) / len(per_case) if per_case else None
    overage = max(0.0, observed / budget - 1.0) if observed is not None and budget else 0.0
    return CreditUse(mean_credits=None if observed is None else round(observed, 6), budget=budget,
                     overage=round(overage, 4))


# ---- code-decided checks

class CheckKind(StrEnum):
    JUDGE = "judge"
    DB = "db"
    ACTION = "action"
    COMMUNICATE = "communicate"


class CheckResult(BaseModel):
    """One graded row. Judge rows carry the question and the judge's answer and quote."""

    kind: CheckKind
    scored: bool = True
    passed: bool
    problem: str = ""
    question: str = ""
    expect: bool | None = None
    answer: Answer | None = None
    quote: str = ""
    observed: list[JsonObject] = []

    @property
    def label(self) -> str:
        return self.question or f"{self.kind}: {self.problem or 'ok'}"


def _state_diff(actual: dict[str, list[JsonObject]], gold: dict[str, list[JsonObject]]) -> dict[str, str]:
    """Per projected table that differs: the rows only the agent wrote and the rows it missed."""
    return {table: f"extra {[r for r in actual[table] if r not in gold[table]]}, "
                   f"missing {[r for r in gold[table] if r not in actual[table]]}"
            for table in billing_store.PROJECTION if actual[table] != gold[table]}


def check_db(expected: DbExpectation, billing: BillingRecord, release: Release) -> CheckResult:
    actual = billing_store.project(billing.state or billing_store.empty())
    baseline = load_db(release)
    diffs = [_state_diff(actual, billing_store.project(billing_store.replay(baseline, actions, release=release)))
             for actions in (expected.gold, *expected.alternatives)]
    closest = min(diffs, key=len)
    problem = "; ".join(f"{table}: {difference}" for table, difference in closest.items())
    return CheckResult(kind=CheckKind.DB, scored=expected.scored, passed=not closest, problem=problem)


def _matches(action: Action, call: ConsoleCall, messages: list[Message]) -> bool:
    if not (call.ok and action.op.matches(call.op, call.table)):
        return False
    if action.op.kind is OperationKind.QUERY:
        account_id = action.equals.get("account_id")
        return account_id is None or account_id in (call.account_ids or [])
    row = call.row or {}
    seen = _received(row, messages[:call.at])
    return (all(row.get(field) == value for field, value in action.equals.items())
            and all(normalized(fact) in seen for fact in action.contains))


def _received(row: JsonObject, before: list[Message]) -> str:
    """What the receiving team sees on a handoff: the row as written and the conversation before it."""
    return normalized(f"{json.dumps(row)} {render(before)}")


def scrub(text: str) -> str:
    """Lowercase ASCII letters and digits only, so "4111-1111" and "4111 1111" compare equal."""
    return "".join(character for character in text.lower() if character.isalnum() and character.isascii())


def _leaks(action: Action, log: tuple[ConsoleCall, ...]) -> list[str]:
    """Every `excludes` text some successful matching write stored, earlier writes included."""
    return [f"{call.op}:{call.table} at {call.at} stored {needle!r}"
            for call in log if call.ok and call.row is not None and action.op.matches(call.op, call.table)
            for needle in action.excludes if scrub(needle) in scrub(json.dumps(call.row))]


def _brief(action: Action) -> str:
    parts = [f"{key}={value}" for key, value in action.equals.items()] + [repr(fact) for fact in action.contains]
    return f"{action.op}({', '.join(parts)})"


def check_actions(actions: tuple[Action, ...], log: tuple[ConsoleCall, ...], messages: list[Message]) -> CheckResult:
    problems = [f"no successful {_brief(a)}" for a in actions if not any(_matches(a, c, messages) for c in log)]
    problems += [leak for action in actions for leak in _leaks(action, log)]
    observed: list[JsonObject] = [{"op": f"{c.op}:{c.table}", "at": c.at, "ok": c.ok} for c in log]
    return CheckResult(kind=CheckKind.ACTION, passed=not problems, problem="; ".join(problems), observed=observed)


def _plain(text: str) -> str:
    return " ".join(text.lower().replace(",", "").replace("$", "").split())


def _says(form: str, said: str) -> bool:
    """`form` appears in `said` without running into a neighbouring digit, so "4120" never matches "41200"."""
    return re.search(rf"(?<!\d){re.escape(_plain(form))}(?!\d)", said) is not None


def check_communicate(values: tuple[tuple[str, ...], ...], messages: list[Message]) -> CheckResult:
    said = _plain(" ".join(m.content or "" for m in messages if isinstance(m, AssistantMessage)))
    missing = [forms for forms in values if not any(_says(form, said) for form in forms)]
    return CheckResult(kind=CheckKind.COMMUNICATE, passed=not missing,
                       problem="; ".join(f"never said any of {list(forms)}" for forms in missing))


# ---- grading

WORKERS = 8
CASE_ATTEMPTS = 3


class ScenarioGrade(BaseModel):
    id: str
    termination: Termination
    error: str | None = None
    passed: bool
    checks: list[CheckResult]
    credits: float = 0.0


class StepSummary(BaseModel):
    reward: float
    quality: float
    credits: CreditUse
    situations: int
    scenarios: int
    passed: int
    terminations: dict[str, int]
    by_category: dict[str, str]
    by_purpose: dict[str, str]
    by_rule: dict[str, str]
    unscored_failed: list[str]
    unscored_total: int


class CaseRecord(BaseModel):
    """What one scenario leaves in `log_dir/cases/<id>.json`."""

    scenario: Scenario
    result: Result
    grade: ScenarioGrade | None = None
    judge_error: str | None = None


def judge_checks(case: Scenario, messages: list[Message], ask: Ask) -> list[CheckResult]:
    questions = [check.question for check in case.checks]
    answers = ask(render(messages), questions, CONVERSATION_INSTRUCTIONS) if questions else []
    return [CheckResult(kind=CheckKind.JUDGE, scored=check.scored, question=check.question, expect=check.expect,
                        answer=answer.answer, quote=answer.quote, passed=answer.is_yes == check.expect)
            for check, answer in zip(case.checks, answers, strict=True)]


def billing_checks(case: Scenario, result: Result, messages: list[Message], release: Release) -> list[CheckResult]:
    rows = [check_db(case.db, result.billing, release)] if case.db is not None else []
    if case.actions:
        rows.append(check_actions(case.actions, result.billing.log, messages))
    if case.communicate:
        rows.append(check_communicate(case.communicate, messages))
    return rows


def _unfinished(row: CheckResult) -> CheckResult:
    """An unfinished conversation fails every check: its end state is not the agent's answer."""
    return row.model_copy(update={"passed": False, "problem": "the conversation did not finish"})


def grade(case: Scenario, result: Result, ask: Ask = ask_judge, release: Release | None = None) -> ScenarioGrade:
    """`release` is the console the conversation ran on; it defaults to the scenario's own step."""
    messages = [from_dict(m) for m in result.messages]
    finished = result.termination.finished
    checks = billing_checks(case, result, messages, release or case.step.release)
    checks = judge_checks(case, messages, ask) + checks if finished else [_unfinished(row) for row in checks]
    passed = finished and all(row.passed for row in checks if row.scored)
    return ScenarioGrade(id=case.id, termination=result.termination, error=result.error, passed=passed,
                         checks=checks, credits=credits(result.model_calls))


def _tally(groups: Iterable[tuple[str, bool]]) -> dict[str, str]:
    table: dict[str, list[bool]] = defaultdict(list)
    for key, passed in groups:
        table[key].append(passed)
    return {key: f"{sum(values)}/{len(values)}" for key, values in sorted(table.items())}


def _situations(scenarios: list[Scenario], passed: dict[str, bool]) -> dict[str, list[bool]]:
    """Scenario outcomes grouped by the situation they reword."""
    situations: dict[str, list[bool]] = defaultdict(list)
    for s in scenarios:
        situations[s.realization_of or s.id].append(passed[s.id])
    return situations


def _reward(situations: dict[str, list[bool]]) -> float:
    return round(mean(mean(v) for v in situations.values()), 4) if situations else 0.0


def summarize(scenarios: list[Scenario], graded: list[ScenarioGrade]) -> StepSummary:
    """The score averages each situation (a scenario and its rewordings), then the situations.

    The reward is that score less the credit overage (see credit_use), floored at 0.
    """
    passed = {g.id: g.passed for g in graded}
    situations = _situations(scenarios, passed)
    unscored = [(g.id, row) for g in graded for row in g.checks if not row.scored]
    quality = _reward(situations)
    use = credit_use([g.credits for g in graded])
    return StepSummary(
        reward=round(max(0.0, quality - use.overage), 4),
        quality=quality,
        credits=use,
        situations=len(situations),
        scenarios=len(graded),
        passed=sum(passed.values()),
        terminations=dict(Counter(str(g.termination) for g in graded)),
        **_breakdowns(scenarios, passed),
        unscored_failed=[f"{case_id}: {row.label}" for case_id, row in unscored if not row.passed],
        unscored_total=len(unscored),
    )


def _breakdowns(scenarios: list[Scenario], passed: dict[str, bool]) -> dict[str, dict[str, str]]:
    """"passed/total" by category, by purpose, and by the rule each scenario tests."""
    return {
        "by_category": _tally((s.category, passed[s.id]) for s in scenarios),
        "by_purpose": _tally((str(s.purpose), passed[s.id]) for s in scenarios),
        "by_rule": _tally((rule, passed[s.id]) for s in scenarios for rule in s.rules),
    }


def _converse(case: Scenario, workspace: Path, log_dir: Path, guidelines: str, release: Release) -> Result:
    """One conversation; a simulator failure is retried, and the last one raises."""
    for _ in range(CASE_ATTEMPTS - 1):
        try:
            return run_case(case, workspace, log_dir, Customer(case.brief, guidelines), release=release)
        except CustomerError:
            pass
    return run_case(case, workspace, log_dir, Customer(case.brief, guidelines), release=release)


def _grade_and_log(case: Scenario, result: Result, path: Path, release: Release) -> ScenarioGrade:
    record = CaseRecord(scenario=case, result=result)
    try:
        record.grade = grade(case, result, release=release)
        return record.grade
    except JudgeError as error:
        record.judge_error = str(error)
        raise
    finally:
        path.write_text(record.model_dump_json(indent=2) + "\n")


class Selection(BaseModel):
    """Which scenarios of a step to run, and on which console release (default: the step's own)."""

    model_config = ConfigDict(frozen=True)

    only: frozenset[str] | None = None
    release: Release | None = None
    skip_invalid: bool = False
    """Drop a case the simulator or judge could not finish, instead of raising (diagnostic runs only)."""


ALL = Selection()


def run_step(step: Step, workspace: Path, log_dir: Path, *, workers: int = WORKERS,
             select: Selection = ALL) -> tuple[StepSummary, list[ScenarioGrade]]:
    """Converse and grade the selected scenarios of `step`."""
    console = select.release or step.release
    scenarios = load_scenarios(step, select.only)
    guidelines = (TESTS / "run" / "customer_guidelines.md").read_text()
    (log_dir / "cases").mkdir(parents=True, exist_ok=True)

    def one(case: Scenario) -> ScenarioGrade | None:
        try:
            result = _converse(case, workspace, log_dir, guidelines, console)
            return _grade_and_log(case, result, log_dir / "cases" / f"{case.id}.json", console)
        except (CustomerError, JudgeError):
            if not select.skip_invalid:
                raise
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, scenarios))
    kept = [(case, graded) for case, graded in zip(scenarios, results, strict=True) if graded is not None]
    return summarize([case for case, _ in kept], [graded for _, graded in kept]), [graded for _, graded in kept]


def _layered(layers: list[Path], destination: Path) -> Path:
    for layer in layers:
        shutil.copytree(layer, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade a workspace on one step's scenarios, locally.")
    parser.add_argument("step", type=Step, choices=list(Step))
    parser.add_argument("layers", nargs="+", type=Path, help="workspace directories; later files override earlier")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--release", type=Release, choices=list(Release), help="console release (default: the step's)")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "redwood-grade")
    args = parser.parse_args(argv)
    run_agent.KIT_PATH = str(TESTS.parent / "environment")
    only = frozenset(args.only.split(",")) if args.only else None
    with tempfile.TemporaryDirectory() as tmp:
        workspace = _layered(args.layers, Path(tmp) / "workspace")
        summary, graded = run_step(args.step, workspace, args.out, workers=args.workers,
                                   select=Selection(only=only, release=args.release))
    (args.out / "summary.json").write_text(summary.model_dump_json(indent=2) + "\n")
    for g in graded:
        failed = [row.label for row in g.checks if row.scored and not row.passed]
        print(f"{g.id}  {'PASS' if g.passed else 'fail'}  {g.termination}  {'; '.join(failed)}")
    print(summary.model_dump_json(indent=2))
    print(f"logs: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
