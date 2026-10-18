"""Verify one step of the Redwood billing-support task.

    verify.py build_r0 | maintain_r1

Each step checks that the builder ran one Pi session (continued across steps),
records what it changed in the workspace, freezes the workspace, and runs the
step's hidden billing scenarios against it as a user that cannot read /app/R
(see grade/grade.py). The reward is the step's scenario score less any credit overage. Afterwards everything
that user left behind is killed and deleted, so the builder's next session
cannot read the conversations. Environment and simulator problems are
an invalid run (exit 2), not a score.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from evolve.diagnostics import EvolutionReport, Outcomes, attempt, report
from grade.grade import ScenarioGrade, Selection, StepSummary, run_step
from grade.scenarios import Release, Step, load_scenarios
from pydantic import BaseModel
from run.run_agent import Termination, freeze, scrub

APP = Path("/app")
BASELINE = Path("/opt/redwood-baseline/workspace")
RELEASE = Path("/etc/redwood-release")
STATE = Path("/run/redwood-verifier")
LOGS = Path("/logs/verifier")
SESSIONS = Path("/logs/agent/pi/sessions")
TRACKED = ("system_prompt.md", "AGENTS.md")
A0 = STATE / "a0"  # the frozen build_r0 workspace, kept for the maintain_r1 diagnostics
Q00 = STATE / "q00.json"


class Session(BaseModel):
    file: str
    id: str
    bytes: int


class FileSize(BaseModel):
    chars: int
    lines: int


class WorkspaceChanges(BaseModel):
    added: list[str]
    removed: list[str]
    modified: list[str]


class WorkspaceReport(BaseModel):
    files: int
    changed: WorkspaceChanges
    sizes: dict[str, FileSize | None]


class CaseOutcome(BaseModel):
    id: str
    passed: bool
    termination: Termination


class Evidence(BaseModel):
    step: Step
    release: Release
    session: Session
    workspace: WorkspaceReport
    artifact_error: str | None = None
    score: StepSummary | None = None
    cases: list[CaseOutcome] = []
    scrubbed: int | None = None
    evolution: EvolutionReport | None = None
    evolution_error: str | None = None


class Reward(BaseModel):
    reward: float


class InvalidRun(BaseModel):
    error: str


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def write_model(path: Path, model: BaseModel) -> None:
    write_text(path, model.model_dump_json(indent=2) + "\n")


def publish(root: Path) -> None:
    """Hand the output to whoever owns `root`: Harbor bind-mounts it and moves its contents on the host."""
    owner = root.stat()
    for path in root.rglob("*"):
        os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)


def manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def _size(path: Path) -> FileSize | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    return FileSize(chars=len(text), lines=len(text.splitlines()))


def workspace_report(before: dict[str, str], after: dict[str, str], workspace: Path) -> WorkspaceReport:
    changed = WorkspaceChanges(
        added=sorted(after.keys() - before.keys()),
        removed=sorted(before.keys() - after.keys()),
        modified=sorted(p for p in after.keys() & before.keys() if after[p] != before[p]),
    )
    return WorkspaceReport(files=len(after), changed=changed, sizes={name: _size(workspace / name) for name in TRACKED})


def _current_session() -> Session:
    files = sorted(SESSIONS.glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected exactly one Pi session, found {len(files)}")
    with files[0].open() as handle:
        first = json.loads(handle.readline())
    if first.get("type") != "session" or not first.get("id"):
        raise RuntimeError("Pi session does not start with an identified session record")
    return Session(file=files[0].name, id=first["id"], bytes=files[0].stat().st_size)


def check_session(step: Step) -> Session:
    """The one Pi session, which a later step must continue (same file and id, and longer)."""
    session = _current_session()
    if step is not Step.BUILD_R0:
        before = Session.model_validate_json((STATE / "session.json").read_text())
        if (session.file, session.id) != (before.file, before.id):
            raise RuntimeError("Pi started a fresh session instead of continuing the previous step")
        if session.bytes <= before.bytes:
            raise RuntimeError("Pi session did not grow during this step")
    write_model(STATE / "session.json", session)
    return session


def check_environment(step: Step) -> None:
    if (current := RELEASE.read_text().strip()) != step.release:
        raise RuntimeError(f"{step} expects {step.release} but the environment is at {current}")
    if exposed := [p for p in (APP / "R", *(APP / "R").rglob("*")) if p.lstat().st_mode & 0o007]:
        raise RuntimeError(f"{len(exposed)} entries under /app/R are readable by the agent user, e.g. {exposed[0]}")


def passed(graded: list[ScenarioGrade]) -> Outcomes:
    return {g.id: g.passed for g in graded}


def keep_a0(frozen: Path, graded: list[ScenarioGrade]) -> None:
    if A0.exists():
        shutil.rmtree(A0)
    shutil.copytree(frozen, A0)
    write_text(Q00, json.dumps(passed(graded), indent=2) + "\n")


def evolution_report(a1: Path, q11: list[ScenarioGrade]) -> EvolutionReport:
    """Run Q01 (A0 on R1 cases) and Q10 (A1 on R0 cases, R1 console), then the diagnostics.

    A failed run loses only the diagnostics that need it; a case the simulator
    could not finish is left out of the comparison instead of failing the whole run.
    """
    a0 = Path("/run/redwood-agent-A0")
    freeze(A0, a0)
    logs = {"q11": LOGS, "q01": LOGS / "q01", "q10": LOGS / "q10"}
    errors: dict[str, str] = {}
    q01 = attempt(errors, "q01", lambda: passed(run_step(Step.MAINTAIN_R1, a0, logs["q01"],
                                                         select=Selection(skip_invalid=True))[1]))
    q10 = attempt(errors, "q10", lambda: passed(run_step(Step.BUILD_R0, a1, logs["q10"],
                                                         select=Selection(release=Release.R1, skip_invalid=True))[1]))
    q00: Outcomes = json.loads(Q00.read_text())
    runs = {"q00": q00, "q01": q01, "q10": q10, "q11": passed(q11)}
    rules = {s.id: s.rules for s in load_scenarios(Step.MAINTAIN_R1)}
    evolution = report(a0, a1, runs, logs, rules)
    evolution.errors = {**errors, **evolution.errors}
    return evolution


def diagnose(step: Step, frozen: Path, graded: list[ScenarioGrade], evidence: Evidence) -> None:
    """Keep A0 after build_r0; after maintain_r1 add the evolution report. Never changes the reward."""
    if step is Step.BUILD_R0:
        keep_a0(frozen, graded)
        return
    try:
        evidence.evolution = evolution_report(frozen, graded)
    except Exception as error:  # a diagnostic failure is reported, not scored
        evidence.evolution_error = f"{type(error).__name__}: {error}"


def finish(evidence: Evidence, reward: float) -> None:
    write_model(LOGS / "evidence.json", evidence)
    write_model(LOGS / "reward.json", Reward(reward=reward))
    publish(LOGS)


def run(step: Step) -> int:
    check_environment(step)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    session = check_session(step)
    before = manifest(BASELINE) if step is Step.BUILD_R0 else json.loads((STATE / "manifest.json").read_text())
    after = manifest(APP / "workspace")
    write_text(STATE / "manifest.json", json.dumps(after, indent=2, sort_keys=True) + "\n")
    evidence = Evidence(step=step, release=step.release, session=session,
                        workspace=workspace_report(before, after, APP / "workspace"))

    frozen = Path(f"/run/redwood-agent-{step.release}")
    try:
        freeze(APP / "workspace", frozen)
    except ValueError as error:
        evidence.artifact_error = str(error)
        finish(evidence, 0.0)
        return 0
    try:
        summary, graded = run_step(step, frozen, LOGS)
        diagnose(step, frozen, graded, evidence)
    finally:
        evidence.scrubbed = scrub()
    evidence.score = summary
    evidence.cases = [CaseOutcome(id=g.id, passed=g.passed, termination=g.termination) for g in graded]
    finish(evidence, summary.reward)
    print(f"{step}: {summary.passed}/{summary.scenarios} scenarios, reward {summary.reward}; "
          f"sizes {evidence.workspace.sizes}")
    return 0


def main() -> int:
    try:
        if os.geteuid() != 0:
            raise RuntimeError("the step verifier must run as root")
        steps = [str(step) for step in Step]
        if len(sys.argv) != 2 or sys.argv[1] not in steps:
            raise ValueError(f"usage: verify.py {'|'.join(steps)}")
        return run(Step(sys.argv[1]))
    except Exception as error:
        write_model(LOGS / "invalid-run.json", InvalidRun(error=f"{type(error).__name__}: {error}"))
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
