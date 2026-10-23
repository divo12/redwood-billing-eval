"""Score a finished run from its four graded Q runs.

    python -m grade.final <out> <a0 workspace> <a1 workspace>

<out>/q00 ... <out>/q11 are the --out directories of `grade.grade` (scripts/grade_snapshots.sh):

    q00  A0 on R0 cases    q01  A0 on R1 cases
    q10  A1 on R0 cases    q11  A1 on R1 cases

reward.json is the gate below; scores.json holds the pass rates and the diagnostics, never the reward.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from evolve.diagnostics import Outcomes, report
from grade.scenarios import Step, load_scenarios

QS = ("q00", "q01", "q10", "q11")


def outcomes(run: Path) -> Outcomes | None:
    """Case id -> passed for one Q run, or None when it was not graded."""
    records = [json.loads(path.read_text()) for path in sorted((run / "cases").glob("*.json"))]
    graded = {r["grade"]["id"]: bool(r["grade"]["passed"]) for r in records if r.get("grade")}
    return graded or None


def rate(run: Outcomes) -> float:
    return round(sum(run.values()) / len(run), 4)


def gate(q00: float, q10: float, q11: float) -> float:
    """1.0: a good build that was maintained without regressing. 0.5: a good build that did not regress."""
    kept = q10 >= q00 - 0.1  # ponytail: one case of slack at 10 cases; widen with more cases or attempts
    if q00 >= 0.9 and q11 >= 0.9 and kept:
        return 1.0
    if q00 >= 0.9 and kept:
        return 0.5
    return 0.0


def score(out: Path, a0: Path, a1: Path) -> tuple[dict[str, float], dict[str, object]]:
    runs = {q: outcomes(out / q) for q in QS}
    rates = {q: rate(r) for q, r in runs.items() if r is not None}
    if missing := [q for q in ("q00", "q10", "q11") if q not in rates]:
        raise SystemExit(f"cannot score: {', '.join(missing)} not graded under {out}")
    rules = {s.id: s.rules for s in load_scenarios(Step.MAINTAIN_R1)}
    diagnostics = report(a0, a1, runs, {q: out / q for q in QS}, rules)
    reward = {"reward": gate(rates["q00"], rates["q10"], rates["q11"])}
    return reward, {"q": rates, **diagnostics.model_dump()}


def main(argv: list[str]) -> int:
    out, a0, a1 = (Path(arg) for arg in argv)
    reward, scores = score(out, a0, a1)
    (out / "reward.json").write_text(json.dumps(reward, indent=2) + "\n")
    (out / "scores.json").write_text(json.dumps(scores, indent=2) + "\n")
    print(json.dumps({**reward, "q": scores["q"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
