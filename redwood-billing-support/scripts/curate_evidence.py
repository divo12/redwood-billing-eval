"""Copy the reviewable slice of one Harbor trial into evidence/<label>/ (jobs/ itself is gitignored and multi-GB).

    python scripts/curate_evidence.py jobs/<job>/<trial> <label>

Kept: the builder's raw Claude Code stream and session per step, the step verifier output, the grading
summary (reward.json, scores.json, q*.txt), each graded case as {grade, termination, transcript} (the model-call
payloads are dropped), and the delivered workspaces' source (files under 1 MB: no indexes).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 1_000_000


def _copy(source: Path, target: Path) -> None:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _case(path: Path) -> dict[str, object]:
    record = json.loads(path.read_text())
    result = record["result"]
    transcript = [{key: message[key] for key in ("role", "content", "tool_calls") if message.get(key)}
                  for message in result["messages"]]
    return {"id": record["scenario"]["id"], "grade": record["grade"], "termination": result["termination"],
            "invalid": result.get("invalid"), "seconds": result.get("seconds"), "transcript": transcript}


def _workspace(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.stat().st_size < MAX_SOURCE_BYTES and "__pycache__" not in path.parts:
            _copy(path, target / path.relative_to(source))


def curate(trial: Path, out: Path) -> None:
    for step in ("build_r0", "maintain_r1"):
        steps = trial / "steps" / step
        for name in ("claude-code.txt", "trajectory.json"):
            _copy(steps / "agent" / name, out / step / "agent" / name)
        for path in (steps / "verifier").glob("*"):
            _copy(path, out / step / "verifier" / path.name)
        _workspace(steps / "artifacts" / "app" / "workspace", out / step / "workspace")
    graded = trial / "graded"
    for path in [*graded.glob("*.json"), *graded.glob("q*.txt")]:
        _copy(path, out / "graded" / path.name)
    for case in sorted(graded.glob("q*/cases/*.json")):
        target = out / "graded" / case.parent.parent.name / case.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_case(case), indent=1) + "\n")


def main(argv: list[str]) -> int:
    trial, label = Path(argv[0]), argv[1]
    out = TASK / "evidence" / label
    shutil.rmtree(out, ignore_errors=True)
    curate(trial, out)
    print(f"{out}: {sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
