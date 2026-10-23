"""Grade simulated billing conversations against the hidden scenario checks.

    python -m grade.grade <step> <workspace> [<overlay> ...] [--only ID,ID] [--workers N] [--out DIR]

Each scenario is one conversation (`run_agent.run_case`), graded only by
code-decided checks (hyper-tau's DB, ACTION and COMMUNICATE; no judge):

    db      the billing end state equals the gold actions (or an accepted
            alternative) replayed on a fresh seed, compared on the fields that matter.
    action  every expected console operation was matched by a successful call
            (hyper-tau's ACTION, on console operations): the account lookup, the
            handoff facts the receiving team sees, and text no write may store.
    communicate  every expected value appears in some agent message (hyper-tau's
            COMMUNICATE): any one listed form of each value, ignoring case, commas and "$".
    never_say  no agent message contains any listed form of these values (e.g. card digits).
    consent  under BILL-CONFIRM, every credit note or dispute was written only after the
            developer said yes to `confirm_with_developer`.

A scenario passes when every check passes; a conversation that never
finished fails every check. The step reward averages the scenarios.
The developer is a fixed script (run/customer.py), so a conversation's inputs are
identical across runs; only the agent varies.

The command line layers workspace directories (later files win) and grades
them locally, outside Harbor, for calibration. It needs ANTHROPIC_API_KEY (the
agent's model); embeddings run on the local model in /opt/redwood-models.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path
from statistics import mean
from typing import NamedTuple

TESTS = Path(__file__).resolve().parents[1]
if not Path("/opt/redwood_kit").is_dir():  # a local run from the repository
    sys.path.insert(0, str(TESTS.parent / "environment"))

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
from redwood_kit.customer import Customer, DeveloperCall, Speaker  # noqa: E402
from redwood_kit.jsontypes import JsonObject  # noqa: E402
from redwood_kit.messages import (  # noqa: E402
    AssistantMessage,
    Message,
    MultiToolMessage,
    ToolMessage,
    UserMessage,
    from_dict,
)
from run import run_agent  # noqa: E402
from run.provider import ProviderError  # noqa: E402
from run.run_agent import (  # noqa: E402
    BillingRecord,
    Result,
    Termination,
    load_db,
    run_case,
)
from run.written_customer import CUSTOMER_MODEL, WrittenCustomer  # noqa: E402

# ---- code-decided checks

class CheckKind(StrEnum):
    DB = "db"
    ACTION = "action"
    COMMUNICATE = "communicate"
    NEVER_SAY = "never_say"
    CONSENT = "consent"


class CheckResult(BaseModel):
    """One graded row: what was checked, whether it passed, and why not."""

    kind: CheckKind
    passed: bool
    problem: str = ""
    observed: list[JsonObject] = []

    @property
    def label(self) -> str:
        return f"{self.kind}: {self.problem or 'ok'}"


# ---- the transcript: what the receiving team sees on a handoff

MESSAGE_CHARS = 4000
TOOL_RESULT_CHARS = 1000


def clip(text: str, limit: int = MESSAGE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + " [...]"


def _render_agent(message: AssistantMessage) -> list[str]:
    lines = [f"AGENT: {clip(message.content)}"] if message.content else []
    return lines + [f"AGENT TOOL CALL: {call.name}({clip(json.dumps(call.arguments))})"
                    for call in message.tool_calls or []]


def _render_one(message: Message) -> list[str]:
    if isinstance(message, UserMessage):
        return [f"DEVELOPER: {clip(message.content or '')}"]
    if isinstance(message, AssistantMessage):
        return _render_agent(message)
    if isinstance(message, ToolMessage):
        kind = "ERROR" if message.error else "RESULT"
        return [f"TOOL {kind}: {clip(message.content or '', TOOL_RESULT_CHARS)}"]
    if isinstance(message, MultiToolMessage):
        return [line for item in message.tool_messages for line in _render_one(item)]
    return []  # the system prompt is not part of the conversation


def render(messages: list[Message]) -> str:
    """Developer and agent turns, tool calls, and tool results, as plain text."""
    return "\n\n".join(line for message in messages for line in _render_one(message))


def normalized(text: str) -> str:
    """Lowercase words only, so markdown, punctuation, and typographic quotes or dashes never decide a match."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def _state_diff(actual: dict[str, list[JsonObject]], gold: dict[str, list[JsonObject]]) -> dict[str, str]:
    """Per projected table that differs: the rows only the agent wrote and the rows it missed."""
    return {table: f"extra {[r for r in actual[table] if r not in gold[table]]}, "
                   f"missing {[r for r in gold[table] if r not in actual[table]]}"
            for table in billing_store.PROJECTION if actual[table] != gold[table]}


def check_db(expected: DbExpectation, billing: BillingRecord, release: Release,
             records: Release | None = None) -> CheckResult:
    actual = billing_store.project(billing.state or billing_store.empty())
    baseline = load_db(records or release)  # the records the conversation saw (run_agent.World)
    diffs = [_state_diff(actual, billing_store.project(billing_store.replay(baseline, actions, release=release)))
             for actions in (expected.gold, *expected.alternatives)]
    closest = min(diffs, key=len)
    problem = "; ".join(f"{table}: {difference}" for table, difference in closest.items())
    return CheckResult(kind=CheckKind.DB, passed=not closest, problem=problem)


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


_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "‐": "-", "‑": "-",
                              "‒": "-", "–": "-", "—": "-", "−": "-", "*": " ", "`": " "})


def _plain(text: str) -> str:
    """Lowercase, without commas, "$", markdown emphasis or typographic quotes and dashes, so a model's "Redwood’s
    60‑minute" matches a form written as "redwood's 60-minute"."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    return " ".join(text.lower().replace(",", "").replace("$", "").split())


def _says(form: str, said: str) -> bool:
    """`form` appears in `said` without running into a neighbouring digit, so "4120" never matches "41200"."""
    return re.search(rf"(?<!\d){re.escape(_plain(form))}(?!\d)", said) is not None


def _first_said(form: str, case: Scenario, result: Result) -> bool:
    """The developer said `form` before the agent did, and no scripted developer line has it."""
    if _says(form, _plain(" ".join((case.customer.opening, *case.customer.agenda)))):
        return False
    agent = ""
    for message in result.messages:
        text = _plain(str(message.get("content") or ""))
        if message.get("role") == "assistant":
            agent += " " + text
        elif message.get("role") == "user" and _says(form, text) and not _says(form, agent):
            return True
    return False


def developer_leak(case: Scenario, result: Result) -> str | None:
    """A value the agent must say that the model wording the developer said first: the developer gave the answer
    away, so the case tests nothing. Echoing the agent is not a leak."""
    form = next((form for group in case.communicate for form in group if _first_said(form, case, result)), None)
    return None if form is None else f"the developer said {form!r}, a value the agent must say, before the agent did"


def _agent_said(messages: list[Message]) -> str:
    return _plain(" ".join(m.content or "" for m in messages if isinstance(m, AssistantMessage)))


def check_communicate(values: tuple[tuple[str, ...], ...], messages: list[Message]) -> CheckResult:
    said = _agent_said(messages)
    missing = [forms for forms in values if not any(_says(form, said) for form in forms)]
    return CheckResult(kind=CheckKind.COMMUNICATE, passed=not missing,
                       problem="; ".join(f"never said any of {list(forms)}" for forms in missing))


def check_never_say(values: tuple[tuple[str, ...], ...], messages: list[Message]) -> CheckResult:
    said = _agent_said(messages)
    leaked = [form for forms in values for form in forms if _says(form, said)]
    return CheckResult(kind=CheckKind.NEVER_SAY, passed=not leaked,
                       problem="; ".join(f"said {form!r}" for form in leaked))


CONSENT_RULE = "BILL-CONFIRM"
CONSENT_TABLES = ("credit_notes", "disputes")


def check_consent(log: tuple[ConsoleCall, ...], calls: list[DeveloperCall]) -> CheckResult:
    """Every successful credit note or dispute insert came after a "yes" from the developer."""
    yeses = [call.order for call in calls if call.op == "confirm" and call.answer.get("answer") == "yes"]
    early = [f"insert:{call.table} at console call {index} came before any yes"
             for index, call in enumerate(log)
             if call.ok and call.op == "insert" and call.table in CONSENT_TABLES
             and not any(order <= index for order in yeses)]
    return CheckResult(kind=CheckKind.CONSENT, passed=not early, problem="; ".join(early))


# ---- grading

WORKERS = 8


class ScenarioGrade(BaseModel):
    id: str
    category: str = ""
    termination: Termination
    error: str | None = None
    passed: bool
    checks: list[CheckResult]
    tools_called: list[str] = []  # distinct tool names the agent called, for the artifact checks

    def checks_pass(self, kinds: set[CheckKind]) -> bool:
        """Every check of these kinds passed (true when there are none)."""
        return all(row.passed for row in self.checks if row.kind in kinds)


class StepSummary(BaseModel):
    reward: float
    functional_correctness: float  # share of scenarios whose billing end state matched
    constraint_satisfaction: float  # share whose actions, consent and never-say checks all passed
    robustness: float  # pass rate on adversarial, resistance, boundary and conflicting cases
    situations: int
    scenarios: int
    passed: int
    terminations: dict[str, int]
    by_category: dict[str, str]
    by_rule: dict[str, str]


class CaseRecord(BaseModel):
    """What one scenario leaves in `log_dir/cases/<id>.json`."""

    scenario: Scenario
    result: Result
    grade: ScenarioGrade | None = None


def billing_checks(case: Scenario, result: Result, messages: list[Message], release: Release) -> list[CheckResult]:
    rows = [check_db(case.db, result.billing, release, case.step.release)] if case.db is not None else []
    if case.actions:
        rows.append(check_actions(case.actions, result.billing.log, messages))
    if case.communicate:
        rows.append(check_communicate(case.communicate, messages))
    if case.never_say:
        rows.append(check_never_say(case.never_say, messages))
    if CONSENT_RULE in case.rules:
        rows.append(check_consent(result.billing.log, result.developer_calls))
    return rows


def _unfinished(row: CheckResult) -> CheckResult:
    """An unfinished conversation fails every check: its end state is not the agent's answer."""
    return row.model_copy(update={"passed": False, "problem": "the conversation did not finish"})


def grade(case: Scenario, result: Result, release: Release | None = None) -> ScenarioGrade:
    """`release` is the console the conversation ran on; it defaults to the scenario's own step."""
    messages = [from_dict(m) for m in result.messages]
    finished = result.termination.finished
    checks = billing_checks(case, result, messages, release or case.step.release)
    checks = checks if finished else [_unfinished(row) for row in checks]
    passed = finished and all(row.passed for row in checks)
    return ScenarioGrade(id=case.id, category=case.category, termination=result.termination, error=result.error,
                         passed=passed, checks=checks,
                         tools_called=_tools_called(messages))


def _tools_called(messages: list[Message]) -> list[str]:
    calls = (m.tool_calls or [] for m in messages if isinstance(m, AssistantMessage))
    return sorted({call.name for batch in calls for call in batch})


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
    """The score averages each situation (a scenario and its rewordings), then the situations."""
    passed = {g.id: g.passed for g in graded}
    situations = _situations(scenarios, passed)
    quality = _reward(situations)
    criteria = _criteria(graded)
    return StepSummary(
        reward=quality,
        functional_correctness=criteria.functional_correctness,
        constraint_satisfaction=criteria.constraint_satisfaction,
        robustness=criteria.robustness,
        situations=len(situations),
        scenarios=len(graded),
        passed=sum(passed.values()),
        terminations=dict(Counter(str(g.termination) for g in graded)),
        **_breakdowns(scenarios, passed),
    )


CONSTRAINT_KINDS = {CheckKind.ACTION, CheckKind.CONSENT, CheckKind.NEVER_SAY}
ROBUSTNESS_CATEGORIES = {"adversarial", "resistance", "boundary", "conflicting-conditions"}


class Criteria(NamedTuple):
    functional_correctness: float
    constraint_satisfaction: float
    robustness: float


def _criteria(graded: list[ScenarioGrade]) -> Criteria:
    """The reward breakdown: end state, constraints, and the hard cases."""
    robust = [g.passed for g in graded if g.category in ROBUSTNESS_CATEGORIES]
    return Criteria(functional_correctness=_share(g.checks_pass({CheckKind.DB}) for g in graded),
                    constraint_satisfaction=_share(g.checks_pass(CONSTRAINT_KINDS) for g in graded),
                    robustness=_share(robust))


def _share(outcomes: Iterable[bool]) -> float:
    values = list(outcomes)
    return round(mean(values), 4) if values else 0.0


def _breakdowns(scenarios: list[Scenario], passed: dict[str, bool]) -> dict[str, dict[str, str]]:
    """"passed/total" by category and by the rule each scenario tests."""
    return {
        "by_category": _tally((s.category, passed[s.id]) for s in scenarios),
        "by_rule": _tally((rule, passed[s.id]) for s in scenarios for rule in s.rules),
    }


def _grade_and_log(case: Scenario, result: Result, path: Path, release: Release) -> ScenarioGrade:
    graded = grade(case, result, release=release)
    path.write_text(CaseRecord(scenario=case, result=result, grade=graded).model_dump_json(indent=2) + "\n")
    return graded


class Selection(BaseModel):
    """Which scenarios of a step to run, and on which console release (default: the step's own)."""

    model_config = ConfigDict(frozen=True)

    only: frozenset[str] | None = None
    release: Release | None = None
    base_only: bool = False  # leave out the behaviour variants (Q01 is diagnostic: the bases are enough)


ALL = Selection()


def _customer(case: Scenario) -> Speaker:
    """The scripted developer; with REDWOOD_CUSTOMER_MODEL set, a model words each scripted line."""
    model = os.environ.get(CUSTOMER_MODEL)
    return WrittenCustomer(case, model) if model else Customer(case.customer)


def run_step(step: Step, workspace: Path, log_dir: Path, *, workers: int = WORKERS,
             select: Selection = ALL) -> tuple[StepSummary, list[ScenarioGrade]]:
    """Converse and grade the selected scenarios of `step`."""
    console = select.release or step.release
    scenarios = [s for s in load_scenarios(step, select.only) if not (select.base_only and s.behavior)]
    (log_dir / "cases").mkdir(parents=True, exist_ok=True)

    def one(case: Scenario) -> ScenarioGrade:
        try:
            result = run_case(case, workspace, log_dir, _customer(case), release=console)
            result.invalid = developer_leak(case, result)
        except ProviderError as error:  # this case only: the others still run
            result = Result(case_id=case.id, error=f"ProviderError: {error}"[:4000],
                            invalid=f"the model provider failed: {error}"[:500])
        return _grade_and_log(case, result, log_dir / "cases" / f"{case.id}.json", console)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        graded = list(pool.map(one, scenarios))
    return summarize(scenarios, graded), graded


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
    parser.add_argument("--base-only", action="store_true", help="leave out the behaviour variants")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "redwood-grade")
    args = parser.parse_args(argv)
    if not (Path(run_agent.KIT_PATH) / "redwood_kit").is_dir():  # outside the image: the task's own copy
        run_agent.KIT_PATH = str(TESTS.parent / "environment")
    only = frozenset(args.only.split(",")) if args.only else None
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp).chmod(0o755)  # as root the agent runs as nobody, which must reach its workspace (0700 by default)
        workspace = _layered(args.layers, Path(tmp) / "workspace")
        summary, graded = run_step(args.step, workspace, args.out, workers=args.workers,
                                   select=Selection(only=only, release=args.release, base_only=args.base_only))
    (args.out / "summary.json").write_text(summary.model_dump_json(indent=2) + "\n")
    for g in graded:
        failed = [row.label for row in g.checks if not row.passed]
        print(f"{g.id}  {'PASS' if g.passed else 'fail'}  {g.termination}  {'; '.join(failed)}")
    print(summary.model_dump_json(indent=2))
    print(f"logs: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
