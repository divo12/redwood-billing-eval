"""The diagnostics over the four Q runs. None of it is part of the reward.

    health      per-case means of seconds, model calls, tokens, embedding calls and customer
                turns; lines per workspace layer and bytes of everything else.
    drift       Q01 (A0 on R1 cases) vs Q11 (A1 on R1 cases): cases A1 absorbed.
    regression  Q00 (A0 on R0 cases) vs Q10 (A1 on them, R1 console): cases lost.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel
from run.run_agent import Result

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


# ---- health

LAYERS = {
    "system_prompt": ("system_prompt.md",),
    "agents": ("AGENTS.md",),
    "skills": ("skills/**/*.md",),
    "subagents": ("subagents/*.md",),
    "hooks": ("hooks.py",),
    "tools": ("tools.py",),
}


class CaseCost(BaseModel):
    """What one conversation cost."""

    seconds: float
    model_calls: float
    prompt_tokens: float
    completion_tokens: float
    embedding_calls: float
    customer_turns: float

    @classmethod
    def of(cls, result: Result) -> CaseCost:
        return cls(
            seconds=result.seconds,
            model_calls=len(result.model_calls),
            prompt_tokens=sum(call.prompt_tokens for call in result.model_calls),
            completion_tokens=sum(call.completion_tokens for call in result.model_calls),
            embedding_calls=len(result.embedding_calls),
            customer_turns=sum(message.get("role") == "user" for message in result.messages),
        )


class RunHealth(CaseCost):
    """Per-case means of `CaseCost` over one run, and over only its passing cases, and how its conversations ended.

    Compare runs on `passing`: an agent that fails early looks cheap over all cases. `ends` counts terminations,
    with `agent_error` split by kind (`error_kind`) and invalid cases counted apart.
    """

    cases: int
    passing: CaseCost | None
    ends: dict[str, int] = {}


# How an agent_error ended, from the error the conversation recorded (run_agent, redwood_kit.conversation).
ERROR_KINDS = (("candidate process exited or timed out", "crash or timeout"),
               ("candidate error", "agent exception"),
               ("neither text nor tool calls", "bad message"))


def error_kind(error: str | None) -> str:
    return next((kind for text, kind in ERROR_KINDS if text in (error or "")), "other")


def ending(result: Result) -> str:
    if result.invalid:
        return "invalid"
    if result.termination == "agent_error":
        return f"agent_error: {error_kind(result.error)}"
    return str(result.termination)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def run_health(log_dir: Path) -> RunHealth:
    """Per-case means over the case records `grade.run_step` wrote to `log_dir/cases`."""
    records = [json.loads(path.read_text()) for path in sorted((log_dir / "cases").glob("*.json"))]
    results = [Result.model_validate(r["result"]) for r in records]  # only the result: older records stay readable
    passing = [result for r, result in zip(records, results, strict=True) if r.get("grade") and r["grade"]["passed"]]
    ends = dict(Counter(ending(result) for result in results))
    return RunHealth(cases=len(records), passing=_means(passing) if passing else None, ends=ends,
                     **_means(results).model_dump())


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
    errors: dict[str, str] = {}


def attempt[T](errors: dict[str, str], name: str, compute: Callable[[], T]) -> T | None:
    """`compute()`, or None with the failure recorded under `name`, so one part never loses the others."""
    try:
        return compute()
    except Exception as error:
        errors[name] = f"{type(error).__name__}: {error}"
        return None


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
        errors=errors,
    )
