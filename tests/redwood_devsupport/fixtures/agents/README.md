# Verifier calibration agents

Workspace layers for `tasks/redwood-devsupport/tests/grade.py`, which copies
the directories in order (later files win) and grades the result on one
step's hidden scenarios. Run from the repository root with `OPENAI_API_KEY`
set. `S`, `O0`, and `O1` stand for these layers:

```sh
S=tasks/redwood-devsupport/environment/app/workspace          # starting workspace
O0=tasks/redwood-devsupport/steps/build_r0/solution/workspace  # oracle A0
O1=tasks/redwood-devsupport/steps/maintain_r1/solution/workspace  # oracle A1 (policies only)
F=tests/redwood_devsupport/fixtures/agents
GRADE="uv run --python 3.12 --no-project --with openai --with pydantic python tasks/redwood-devsupport/tests/grade.py"

$GRADE build_r0 $S $O0                          # oracle A0: 0.97 (Harbor), 1.0 after the a0-28 check fix
$GRADE maintain_r1 $S $O0 $O1                   # oracle A1: 1.0 (Harbor)
$GRADE maintain_r1 $S $O0                       # stale A1: regression passes; every update and staleness scenario fails
$GRADE build_r0 $S                              # starting workspace: 0.64 (no Redwood knowledge, careful model)
$GRADE build_r0 $S $O0 $F/escalate_everything   # 0.97, like the oracle; every unscored no-handoff ticket fails
$GRADE build_r0 $S $F/refuse_all                # 0
$GRADE build_r0 $S $F/stop_early                # 0
$GRADE build_r0 $S $F/always_escalate           # 0.10: a0-27, a0-30, a0-31, where routing without a promise is right
$GRADE build_r0 $S $F/judge_injection           # 0
```

Scores are from single runs on 2026-09-25 with simulator gpt-5.5, judge gpt-5.6-sol at low effort, and agent gpt-6-sol at the builder's chosen effort, before the models were pinned to their current settings.

The last four make no model calls of their own; only the simulated developer
and the judge cost anything.
