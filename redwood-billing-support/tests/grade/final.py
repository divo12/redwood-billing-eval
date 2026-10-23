"""Score a finished run from its four graded Q runs.

    python -m grade.final <out> <a0 workspace> <a1 workspace>

<out>/q00 ... <out>/q11 are the --out directories of `grade.grade` (scripts/grade_snapshots.sh):

    q00  A0 on R0 cases    q01  A0 on R1 cases
    q10  A1 on R0 cases    q11  A1 on R1 cases

reward.json holds numbers only (Harbor reads it as dict[str, float]), each higher-is-better:

    reward                 the gate below over Q00, Q10, Q11
    q00, q11               the build's and the maintained agent's pass rates (all four rates: scores.json)
    drift                  of the R1 cases A0 fails (Q01), the share A1 passes (Q11): the change absorbed
    regression             of the R0 cases A0 passes (Q00), the share A1 still passes (Q10): 1.0 = nothing broken
    behaviour_robustness   over Q00, Q10, Q11, of the behaviour variants whose base case passed, the share that
                           passed too (missing_info, ambiguous, conflict; per behaviour in scores.json)

A score with nothing to measure is 0.0, no evidence: drift when A0 already passes every R1 case, regression
when A0 passes no R0 case, behaviour_robustness when no base case passed. drift needs Q01 and is left out
without it. scores.json holds the pass rates and the diagnostics.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from evolve.diagnostics import CaseChange, EvolutionReport, Outcomes, report
from grade.scenarios import Scenario, Step, load_scenarios
from pydantic import BaseModel

QS = ("q00", "q01", "q10", "q11")


class _Graded(BaseModel):
    id: str
    passed: bool


class _Outcome(BaseModel):
    invalid: str | None = None


class _Record(BaseModel):
    """The parts of a case record (grade.grade.CaseRecord) scoring reads; older records stay readable."""

    result: _Outcome
    grade: _Graded | None = None


def _records(run: Path) -> list[_Record]:
    return [_Record.model_validate_json(path.read_text()) for path in sorted((run / "cases").glob("*.json"))]


def outcomes(run: Path) -> Outcomes | None:
    """Case id -> passed for one Q run, or None when it was not graded. Invalid cases are left out."""
    graded = {r.grade.id: r.grade.passed for r in _records(run) if r.grade and not r.result.invalid}
    return graded or None


def invalid(run: Path) -> list[dict[str, str]]:
    """Cases whose conversation says nothing about the agent, and why; regrade them with --only."""
    return [{"case": r.grade.id, "why": r.result.invalid} for r in _records(run) if r.grade and r.result.invalid]


def _by_type(run: Outcomes, cases: dict[str, Scenario]) -> dict[str, str]:
    rows: dict[str, list[bool]] = {}
    for case_id, passed in run.items():
        if case_id in cases and cases[case_id].info_type:
            rows.setdefault(str(cases[case_id].info_type), []).append(passed)
    return {kind: f"{sum(row)}/{len(row)}" for kind, row in sorted(rows.items())}


def _twins(run: Outcomes, cases: dict[str, Scenario]) -> tuple[list[tuple[str, str]], list[str]]:
    """Twin pairs graded in this run, and those where only one half passed."""
    twin = {case_id: cases[case_id].twin for case_id in run if case_id in cases}
    pairs = sorted({(min(a, b), max(a, b)) for a, b in twin.items() if b in run})
    return pairs, [f"{a}/{b}" for a, b in pairs if run[a] != run[b]]


def info(runs: dict[str, Outcomes | None]) -> dict[str, dict[str, object]]:
    """Per Q run: pass rate by where the deciding facts come from (info_type), and twin pairs, which count only
    when both halves pass, so an agent that guesses instead of asking gains nothing from the half it guessed."""
    cases = {s.id: s for step in Step for s in load_scenarios(step)}
    out: dict[str, dict[str, object]] = {}
    for q, run in runs.items():
        if run is not None:
            pairs, one_half = _twins(run, cases)
            out[q] = {"by_type": _by_type(run, cases),
                      "twins": f"{sum(run[a] and run[b] for a, b in pairs)}/{len(pairs)}", "one_half": one_half}
    return out


def _rate(results: list[bool]) -> str:
    return f"{sum(results)}/{len(results)}"


def _attribution(run: Outcomes, variants: dict[str, Scenario]) -> dict[str, list[str]]:
    """Each variant against its base (the control): behaviour failure = base passed, variant failed."""
    out: dict[str, list[str]] = {"behaviour failure": [], "handled": [], "both failed": [], "variant only": []}
    for case_id, case in sorted(variants.items()):
        base = run.get(case.variant_of)
        if base is not None:
            kind = ("handled" if run[case_id] else "behaviour failure") if base else \
                ("variant only" if run[case_id] else "both failed")
            out[kind].append(case_id)
    return out


def _robustness_of(run: Outcomes, variants: dict[str, Scenario]) -> dict[str, object]:
    bases = sorted({v.variant_of for v in variants.values()} & set(run))
    by_behavior: dict[str, list[bool]] = {}
    for case_id, case in variants.items():
        by_behavior.setdefault(case.behavior, []).append(run[case_id])
    base_rate = sum(run[b] for b in bases) / len(bases) if bases else 0.0
    variant_rate = sum(run[c] for c in variants) / len(variants)
    return {"bases": _rate([run[b] for b in bases]),
            **{behavior: _rate(results) for behavior, results in sorted(by_behavior.items())},
            "robustness": round(variant_rate / base_rate, 4) if base_rate else None,
            "attribution": _attribution(run, variants)}


def robustness(runs: dict[str, Outcomes | None]) -> dict[str, dict[str, object]]:
    """Per Q run with behaviour variants: pass rate per behaviour, of their bases, robustness (variant pass rate over
    their bases' pass rate; 1.0 = the developer's behaviour costs nothing), and each variant against its base."""
    cases = {s.id: s for step in Step for s in load_scenarios(step)}
    out: dict[str, dict[str, object]] = {}
    for q, run in runs.items():
        variants = {c: cases[c] for c in run or {} if c in cases and cases[c].behavior}
        if run is not None and variants:
            out[q] = _robustness_of(run, variants)
    return out


def invalid_by_q(out: Path) -> dict[str, list[dict[str, str]]]:
    return {q: cases for q in QS if (out / q / "cases").is_dir() and (cases := invalid(out / q))}


def rate(run: Outcomes) -> float:
    return round(sum(run.values()) / len(run), 4)


def _share(results: list[bool]) -> float:
    return round(sum(results) / len(results), 4) if results else 0.0  # nothing to measure: no evidence


def _variants_with_passing_base(run: Outcomes, cases: dict[str, Scenario], kept: dict[str, list[bool]]) -> None:
    for case_id, passed in run.items():
        case = cases.get(case_id)
        if case is not None and case.behavior and run.get(case.variant_of):
            kept.setdefault(case.behavior, []).append(passed)


def behaviour_robustness(runs: dict[str, Outcomes | None]) -> dict[str, float]:
    """Per behaviour and overall: variants that passed, of those whose base passed in the same Q run. A base that
    failed already fails for the rule, so only these variants measure the developer's behaviour alone."""
    cases = {s.id: s for step in Step for s in load_scenarios(step)}
    kept: dict[str, list[bool]] = {}
    for q in ("q00", "q10", "q11"):
        _variants_with_passing_base(runs.get(q) or {}, cases, kept)
    return {"overall": _share([p for row in kept.values() for p in row]),
            **{behavior: _share(row) for behavior, row in sorted(kept.items())}}


def _kept(changes: list[CaseChange], before: bool) -> float:
    """drift (before=False): failing cases now passing; regression (before=True): passing cases still passing."""
    return _share([change.after for change in changes if change.before is before])


def gate(q00: float, q10: float, q11: float) -> float:
    """1.0: a good build that was maintained without regressing. 0.5: a good build that did not regress."""
    kept = q10 >= q00 - 0.1  # ponytail: three cases of slack at 30 cases; widen with more cases or attempts
    if q00 >= 0.9 and q11 >= 0.9 and kept:
        return 1.0
    if q00 >= 0.9 and kept:
        return 0.5
    return 0.0


def _maintenance(diagnostics: EvolutionReport) -> dict[str, float]:
    drift, regression = diagnostics.drift, diagnostics.regression
    return {**({"drift": _kept(drift.cases, before=False)} if drift else {}),
            **({"regression": _kept(regression.cases, before=True)} if regression else {})}


def score(out: Path, a0: Path, a1: Path) -> tuple[dict[str, float], dict[str, object]]:
    runs = {q: outcomes(out / q) for q in QS}
    rates = {q: rate(r) for q, r in runs.items() if r is not None}
    if missing := [q for q in ("q00", "q10", "q11") if q not in rates]:
        raise SystemExit(f"cannot score: {', '.join(missing)} not graded under {out}")
    rules = {s.id: s.rules for s in load_scenarios(Step.MAINTAIN_R1)}
    diagnostics = report(a0, a1, runs, {q: out / q for q in QS}, rules)
    behaviour = behaviour_robustness(runs)
    reward = {"reward": gate(rates["q00"], rates["q10"], rates["q11"]), "q00": rates["q00"], "q11": rates["q11"],
              **_maintenance(diagnostics),
              "behaviour_robustness": behaviour["overall"]}  # flat: Harbor reads reward.json as dict[str, float]
    return reward, {"q": rates, "invalid": invalid_by_q(out), "info": info(runs), "behavior": robustness(runs),
                    "behaviour_robustness": behaviour, **diagnostics.model_dump()}


def main(argv: list[str]) -> int:
    out, a0, a1 = (Path(arg) for arg in argv)
    reward, scores = score(out, a0, a1)
    (out / "reward.json").write_text(json.dumps(reward, indent=2) + "\n")
    (out / "scores.json").write_text(json.dumps(scores, indent=2) + "\n")
    print(json.dumps({**reward, "q": scores["q"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
