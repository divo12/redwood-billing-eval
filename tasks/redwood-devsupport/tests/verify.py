"""Verify one step of the Redwood billing-support task.

    verify.py build_r0 | maintain_r1

Each step checks that the builder ran one agent session (Claude Code or Pi,
continued across steps), records what it changed in the workspace, freezes the
workspace, and runs the step's hidden billing scenarios against it as a user
that cannot read /app/R (see grade/grade.py). The reward is the step's scenario
score less any credit overage; reward.json also breaks it down into functional
correctness, constraint satisfaction, robustness and artifact quality.
Afterwards everything that user left behind is killed and deleted, so the
builder's next session cannot read the conversations. Environment problems are
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
PI_SESSIONS = Path("/logs/agent/pi/sessions")
CLAUDE_SESSIONS = Path("/logs/agent/sessions/projects")  # Harbor's claude-code adapter: CLAUDE_CONFIG_DIR
TRACKED = ("system_prompt.md", "AGENTS.md")
SIZE_LIMITS = {"system_prompt.md": 1500, "AGENTS.md": 4000}  # promised in steps/build_r0/instruction.md
STUB_SKILL = "skills/example/SKILL.md"
STUB_SUBAGENT = "subagents/example.md"
A0 = STATE / "a0"  # the frozen build_r0 workspace, kept for the maintain_r1 diagnostics
Q00 = STATE / "q00.json"


class Session(BaseModel):
    harness: str = "pi"
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
    artifacts: list[ArtifactCheck] = []
    score: StepSummary | None = None
    cases: list[CaseOutcome] = []
    scrubbed: int | None = None
    evolution: EvolutionReport | None = None
    evolution_error: str | None = None


class ArtifactCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Reward(BaseModel):
    """`reward` is what Harbor scores; the rest break it down (the brief's multi-criterion form)."""

    reward: float
    overall: float
    functional_correctness: float
    constraint_satisfaction: float
    robustness: float
    artifact_quality: float


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


def _pi_session() -> Session:
    files = sorted(PI_SESSIONS.glob("*.jsonl"))
    if len(files) != 1:
        raise RuntimeError(f"expected exactly one Pi session, found {len(files)}")
    with files[0].open() as handle:
        first = json.loads(handle.readline())
    if first.get("type") != "session" or not first.get("id"):
        raise RuntimeError("Pi session does not start with an identified session record")
    return Session(harness="pi", file=files[0].name, id=first["id"], bytes=files[0].stat().st_size)


def _claude_session() -> Session:
    """The most recently written top-level Claude Code transcript; its id is the file stem."""
    files = sorted(CLAUDE_SESSIONS.glob("*/*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise RuntimeError(f"no Claude Code session under {CLAUDE_SESSIONS}")
    latest = files[-1]
    return Session(harness="claude-code", file=str(latest.relative_to(CLAUDE_SESSIONS)), id=latest.stem,
                   bytes=latest.stat().st_size)


def _current_session() -> Session:
    if PI_SESSIONS.is_dir() and any(PI_SESSIONS.glob("*.jsonl")):
        return _pi_session()
    return _claude_session()


def _continued(session: Session, before: Session) -> bool:
    """Same transcript grown, or (Claude Code resume) a newer transcript carrying the old session id."""
    if (session.file, session.id) == (before.file, before.id):
        return session.bytes > before.bytes
    if session.harness != "claude-code" or before.harness != "claude-code":
        return False
    return before.id in (CLAUDE_SESSIONS / session.file).read_text(errors="replace")


def check_session(step: Step) -> Session:
    """The builder's session, which a later step must continue rather than start afresh."""
    session = _current_session()
    if step is not Step.BUILD_R0:
        before = Session.model_validate_json((STATE / "session.json").read_text())
        if not _continued(session, before):
            raise RuntimeError(f"{session.harness} did not continue the previous step's session {before.id}")
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
                                                         select=Selection())[1]))
    q10 = attempt(errors, "q10", lambda: passed(run_step(Step.BUILD_R0, a1, logs["q10"],
                                                         select=Selection(release=Release.R1))[1]))
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
    if os.environ.get("REDWOOD_DIAGNOSTICS", "1") == "0":  # skip Q01/Q10: 20 unscored conversations
        evidence.evolution_error = "skipped: REDWOOD_DIAGNOSTICS=0"
        return
    try:
        evidence.evolution = evolution_report(frozen, graded)
    except Exception as error:  # a diagnostic failure is reported, not scored
        evidence.evolution_error = f"{type(error).__name__}: {error}"


def _differs(workspace: Path, relative: str) -> bool:
    baseline = BASELINE / relative
    return not baseline.is_file() or (workspace / relative).read_bytes() != baseline.read_bytes()


def _real(workspace: Path, pattern: str, stub: str) -> list[str]:
    """Files matching `pattern` that are not the starter's example stub."""
    return [path.relative_to(workspace).as_posix() for path in sorted(workspace.glob(pattern))
            if path.relative_to(workspace).as_posix() != stub or _differs(workspace, stub)]


def _exposes(workspace: Path, mixin: str, tool: str) -> bool:
    """tools.py gives the model this tool: through the kit mixin or a method of its own."""
    path = workspace / "tools.py"
    text = path.read_text() if path.is_file() else ""
    return mixin in text or f"def {tool}(" in text


def _size_checks(workspace: Path) -> list[ArtifactCheck]:
    return [ArtifactCheck(name=f"{name} under {limit} characters", passed=size is not None and size.chars < limit,
                          detail=f"{size.chars if size else 'missing'} characters")
            for name, limit in SIZE_LIMITS.items() for size in [_size(workspace / name)]]


def _hook_check(workspace: Path) -> ArtifactCheck:
    hooks = workspace / "hooks.py"
    return ArtifactCheck(name="a @hook guardrail",
                         passed=hooks.is_file() and "@hook" in hooks.read_text() and _differs(workspace, "hooks.py"))


def artifact_checks(workspace: Path, graded: list[ScenarioGrade]) -> list[ArtifactCheck]:
    """The deliverables the instruction promises, checked by code: sizes, components, and that the agent can
    reach them. Whether a conversation used them is reported (`detail`), not required: "when appropriate"."""
    called = {name for g in graded for name in g.tools_called}
    skills = _real(workspace, "skills/*/SKILL.md", STUB_SKILL)
    subagents = _real(workspace, "subagents/*.md", STUB_SUBAGENT)
    return [
        *_size_checks(workspace),
        ArtifactCheck(name="a non-stub skill", passed=bool(skills), detail=", ".join(skills)),
        ArtifactCheck(name="a non-stub subagent", passed=bool(subagents), detail=", ".join(subagents)),
        _hook_check(workspace),
        ArtifactCheck(name="the agent can load the skill (load_skill)",
                      passed=bool(skills) and _exposes(workspace, "SkillTools", "load_skill"),
                      detail=f"loaded in a graded conversation: {'load_skill' in called}"),
        ArtifactCheck(name="the agent can delegate to the subagent (delegate)",
                      passed=bool(subagents) and _exposes(workspace, "SubagentTools", "delegate"),
                      detail=f"delegated in a graded conversation: {'delegate' in called}"),
    ]


def reward_of(summary: StepSummary | None, artifacts: list[ArtifactCheck]) -> Reward:
    quality = round(sum(c.passed for c in artifacts) / len(artifacts), 4) if artifacts else 0.0
    if summary is None:
        return Reward(reward=0.0, overall=0.0, functional_correctness=0.0, constraint_satisfaction=0.0,
                      robustness=0.0, artifact_quality=quality)
    return Reward(reward=summary.reward, overall=summary.reward,
                  functional_correctness=summary.functional_correctness,
                  constraint_satisfaction=summary.constraint_satisfaction,
                  robustness=summary.robustness, artifact_quality=quality)


def finish(evidence: Evidence) -> None:
    write_model(LOGS / "evidence.json", evidence)
    write_model(LOGS / "reward.json", reward_of(evidence.score, evidence.artifacts))
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
        finish(evidence)
        return 0
    if os.environ.get("REDWOOD_DEFER_GRADING") == "1":  # graded later: scripts/grade_snapshots.sh
        evidence.artifacts = artifact_checks(APP / "workspace", [])
        evidence.scrubbed = scrub()
        finish(evidence)  # reward 0.0 here only means "not graded yet"
        print(f"{step}: grading deferred; the workspace is kept in the step artifacts")
        return 0
    try:
        summary, graded = run_step(step, frozen, LOGS)
        diagnose(step, frozen, graded, evidence)
    finally:
        evidence.scrubbed = scrub()
    evidence.score = summary
    evidence.cases = [CaseOutcome(id=g.id, passed=g.passed, termination=g.termination) for g in graded]
    evidence.artifacts = artifact_checks(APP / "workspace", graded)
    finish(evidence)
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
