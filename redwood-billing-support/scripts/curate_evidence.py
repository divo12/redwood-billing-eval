"""Copy the reviewable slice of one Harbor trial into evidence/<label>/ (jobs/ itself is gitignored and multi-GB).

    python scripts/curate_evidence.py jobs/<job>/<trial> <label>

Writes, under evidence/<label>/:

    README.md                        generated: reward, pass rates, every failed case with a link
    reward.json, scores.json         the run's reward and its unscored diagnostics
    builder/                         the builder: one session resumed across both steps
        session.jsonl                    the whole session (Claude Code transcript, or the oracle's Pi record)
        1-build_r0.stream.jsonl          raw stream of step 1 (Harbor's claude-code.txt)
        2-maintain_r1.stream.jsonl       raw stream of step 2
    agents/                          what the builder delivered (source files under 1 MB: no indexes)
        A0-after-build_r0/
        A1-after-maintain_r1/
    step-checks/<step>/              each step verifier's output (deliverable checks, session continuity)
    conversations/<q>/{failed,passed}/<case>.json    {grade, termination, transcript} per graded case
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 1_000_000
STEPS = {"build_r0": ("1", "A0-after-build_r0"), "maintain_r1": ("2", "A1-after-maintain_r1")}
QS = {"q00": "q00-A0-on-R0", "q01": "q01-A0-on-R1-baseline", "q10": "q10-A1-on-R0-regression",
      "q11": "q11-A1-on-R1"}


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


def _builder(trial: Path, out: Path) -> None:
    for step, (number, _) in STEPS.items():
        agent = trial / "steps" / step / "agent"
        _copy(agent / "claude-code.txt", out / "builder" / f"{number}-{step}.stream.jsonl")
        _copy(agent / "oracle.txt", out / "builder" / f"{number}-{step}.oracle.txt")
    last = trial / "steps" / "maintain_r1" / "agent"  # the resumed session holds both steps
    sessions = sorted([*last.glob("sessions/projects/*/*.jsonl"), *last.glob("pi/sessions/*.jsonl")])
    if sessions:
        _copy(sessions[-1], out / "builder" / "session.jsonl")


def _agents_and_checks(trial: Path, out: Path) -> None:
    for step, (_, name) in STEPS.items():
        steps = trial / "steps" / step
        workspace = steps / "artifacts" / "app" / "workspace"
        for path in sorted(workspace.rglob("*")):
            if path.is_file() and path.stat().st_size < MAX_SOURCE_BYTES and "__pycache__" not in path.parts:
                _copy(path, out / "agents" / name / path.relative_to(workspace))
        for path in (steps / "verifier").glob("*"):
            if path.is_file() and path.parent.name == "verifier":
                _copy(path, out / "step-checks" / step / path.name)


def _conversations(graded: Path, out: Path) -> dict[str, dict[str, list[str]]]:
    results: dict[str, dict[str, list[str]]] = {}
    for q, name in QS.items():
        for case in sorted((graded / q / "cases").glob("*.json")):
            record = _case(case)
            grade = record["grade"]
            verdict = "passed" if isinstance(grade, dict) and grade.get("passed") else "failed"
            target = out / "conversations" / name / verdict / case.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(record, indent=1) + "\n")
            results.setdefault(q, {"passed": [], "failed": []})[verdict].append(case.stem)
    return results


def _readme(label: str, out: Path, results: dict[str, dict[str, list[str]]]) -> str:
    reward = json.loads((out / "reward.json").read_text()) if (out / "reward.json").is_file() else {}
    lines = [f"# {label}", "", f"Reward: `{json.dumps(reward)}`", "", "| Q | Passed | Failed cases |", "|---|---|---|"]
    for q, name in QS.items():
        if q in results:
            done = results[q]
            failed = ", ".join(f"[{c}](conversations/{name}/failed/{c}.json)" for c in done["failed"]) or "none"
            lines.append(f"| {name} | {len(done['passed'])}/{len(done['passed']) + len(done['failed'])} | {failed} |")
    lines += ["", "`builder/session.jsonl` is the builder's one session across both steps; `agents/` is what it",
              "delivered; `step-checks/` is each step's verifier output; `scores.json` has the diagnostics.",
              "See `../README.md` for how to read a conversation file.", ""]
    return "\n".join(lines)


def curate(trial: Path, out: Path, label: str) -> None:
    """Graded inside Harbor (maintain_r1's verifier), or afterwards by scripts/grade_snapshots.sh (<trial>/graded)."""
    verifier = trial / "steps" / "maintain_r1" / "verifier"
    graded = trial / "graded" if (trial / "graded").is_dir() else verifier / "graded"
    harbor_reward = verifier / "reward.json"
    graded_in_harbor = harbor_reward.is_file() and "q00" in json.loads(harbor_reward.read_text())  # not a placeholder
    _copy(harbor_reward if graded_in_harbor else graded / "reward.json", out / "reward.json")
    _copy(graded / "scores.json", out / "scores.json")
    _builder(trial, out)
    _agents_and_checks(trial, out)
    (out / "README.md").write_text(_readme(label, out, _conversations(graded, out)))


def main(argv: list[str]) -> int:
    trial, label = Path(argv[0]), argv[1]
    out = TASK / "evidence" / label
    shutil.rmtree(out, ignore_errors=True)
    curate(trial, out, label)
    print(f"{out}: {sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
