# tests/ — the hidden verifier

Harbor copies this folder to `/tests` after each step. The builder never sees it.
It runs the builder's support agent (A) against simulated developers, grades each
conversation, and writes `/logs/verifier/reward.json` plus `evidence.json`.

There are two kinds of output, and they never mix:

| Output | Decides the reward? | Where it is computed |
|---|---|---|
| **Scenario checks** — did A handle this conversation correctly? | **Yes** | `grade/grade.py` (+ `judge.py`) |
| **Credit overage** — mean agent credits per conversation over the published budget | **Yes**, subtracted (0 while no budget is set) | `grade/grade.py` |
| **Evolution diagnostics** — how did A change from A0 to A1? (axes 1–3) | **No**, reported only | `evolve/diagnostics.py` |

## File map

```
verify.py                entry point: verify.py build_r0 | maintain_r1   (orchestration only)
run/                     one conversation
├── run_agent.py         sandboxed agent + billing store + the conversation loop
├── customer.py          the simulated developer (a model playing the customer from a hidden brief)
├── customer_guidelines.md  how the simulated developer behaves (stay in persona, end with `###STOP###`)
└── gateway.py           OpenAI calls for the agent and the customer; the allowed models
grade/                   the reward
├── grade.py             the code-decided checks (db, action), the credit overage
│                        against the published budget; runs every scenario of a step and sums the reward
├── judge.py             transcript rendering + the bounded yes/no LLM judge
└── scenarios.py         typed models of billing_scenarios.json (loaded and validated once)
evolve/
└── diagnostics.py       maintain_r1 only, never scored: axis 1 placement, axis 2 health,
                         axis 3 drift + regression, and the one report object over all axes
data/
├── billing_scenarios.json  20 R0 cases (`build_r0`, ids `a0-*`) and 20 R1 cases (`maintain_r1`, ids `a1-*`);
│                           every R1 case exercises an R1 change (ΔR)
├── delta_r1.json        the R1 changes (axis 1): the rules that show A1 absorbed each, the builder-visible
│                        quote that grounds where it belongs, judge questions and markdown scans
└── provenance.json      which visible R record justifies each rule (`BILL-*`); used by tests, not at grading time
```

## How one step runs (`verify.py`)

`run(step)` does, in order:

1. `check_environment` / `check_session` — the release on the box matches the step, and the builder used one Pi session continued across steps. Failures here are an **invalid run** (exit 2), never a score.
2. `manifest` + `workspace_report` — which workspace files changed since the last step, and their sizes.
3. `freeze` — copy the workspace to a read-only location the agent runs from. A broken artifact (e.g. missing `agent.py`) gives reward 0 with `artifact_error`.
4. `grade.run_step` — converse and grade every scenario of the step (below).
5. `diagnose` — after `build_r0`, keep A0 and its per-case results (Q00). After `maintain_r1`, build the evolution report. Each part (the Q01 and Q10 runs, drift, regression, health, sizes, placement) is computed on its own; a part that fails is `None` with its reason in `evolution.errors`, and the others are kept. A Q01/Q10 case the simulator or judge could not finish is listed as `unmeasured` instead of failing the run. The reward is untouched.
6. `scrub` + `finish` — delete everything the agent left behind, write evidence and reward.

## One scenario (`grade.grade`)

`run_agent.run_case` produces a `Result`: the messages,
the billing store's final state and its call log, plus model/embedding usage.

Then:

1. If the conversation **did not finish** (agent error, step limit, customer gave up),
   the scenario fails. Every billing check is marked failed with
   "the conversation did not finish", and the judge is not asked.
2. Otherwise every check below runs. The scenario **passes when every scored check passes**.
3. The step score is the mean over *situations* (a scenario and its rewordings count once), `grade.summarize`. The reward is that score minus the credit overage, floored at 0 (`grade.credit_use`, published in `app/framework/agent_contract.md`).

Simulator or judge failures are retried (`CASE_ATTEMPTS`, `JUDGE_ATTEMPTS`) and then
raise: an invalid run, not a zero.

## The scenario checks (these make the reward)

Each check returns one `CheckResult` (`grade.py`): `kind`, `passed`, `problem` (why it failed, in words), and `scored`.

### 1. Judge check — `grade.judge_checks`, `judge.ask_judge`

- **Reads:** the rendered transcript (`judge.render`: `DEVELOPER:`, `AGENT:`, `AGENT TOOL CALL:` lines, each clipped).
- **Asks:** the scenario's `checks[].judge` questions, all in one call to the pinned judge model.
- **Passes when:** the judge's answer equals `expect`.
- **Guard rails:** a "yes" must quote the transcript word for word (after `normalized`). A missing answer, an invented quote, or an API error → ask again, up to `JUDGE_ATTEMPTS` (3), then `JudgeError` (invalid run). A retry after unusable answers tells the judge why they were rejected; a retry after an API error backs off first (`JUDGE_BACKOFF_SECONDS`, doubling).
- **Example (a1-03):** "Does the agent say 402 quota_exceeded is the expected response…?" expect `true`; "Does the agent say the 402 is a bug…?" expect `false`.

### 2. DB check — `checks.check_db`

Only when the scenario has `db`.

- **Reads:** the billing store's final state.
- **Compares:** a *projection* of it (`billing.store.PROJECTION`: the fields that matter) against the projection of `db.gold` replayed on a fresh seed of the same release, and against each `db.alternatives` replay.
  - `credit_notes`: account, invoice, lines, amount, reason
  - `disputes`: account, invoice, lines, reason, request ids, status
  - `cases`: account, team, status
- **Passes when:** the projection equals the gold **or** any alternative exactly.
- **Fails with:** for the closest expectation, per table, `extra [...]` rows the agent wrote and `missing [...]` rows it did not.
- **Example:** gold `[]` means "write nothing": any credit, dispute or case fails it.

### 3. Action check — `grade.check_actions`

Only when the scenario has `actions`. Hyper-tau's ACTION, matched on **console operations**
(`ConsoleCall` in the billing log) instead of tool names, so any tool design that reads and
writes the same records passes. Looks only at **successful** calls; order is not graded.

- **Passes when:** every action is matched by some successful call of its `op`
  (`query:`, `insert:`, `update:`, `write:` = insert or update), and no matching write stored
  an `excludes` text. One `action` row per scenario; `problem` lists every unmatched action and leak.
- **`equals`:** for a query, `account_id` is among the accounts it returned (a lookup by email
  counts as much as one by id); for a write, the stored row has these field values.
- **`contains`:** facts the receiving team sees (`grade._received`; a handoff is just a successful `cases` write, read from the call log), in the stored row **or** the conversation
  before the write (the contract: an escalated case hands over the case and the conversation).
  All facts must be on one write.
- **`excludes`:** scrubbed text (lowercase, ASCII letters and digits only) no matching write may
  store, so "4111-1111-1111-1111" and "4111 1111 1111 1111" both leak.
- **Examples:**
  - every write scenario: `{"op": "query:accounts", "equals": {"account_id": "saltmarsh-data"}}`
  - a0-25: `{"op": "write:cases", "equals": {"status": "escalated"}, "contains": ["INV-2026-0831-131", "req_s31", "req_s32"]}`
  - a1-16: `{"op": "write:cases", "excludes": ["4111 1111 1111 1111"]}`
- **Why it exists:** the DB projection ignores reads, `summary`/`details` and free text.

### Checks we deliberately do not have

They would only repeat another check: the escalation team (DB check), ordinary write arguments (DB replay), required/forbidden writes (DB replay), the order of the lookup and the write (hyper-tau's ACTION does not grade order either).

## The evolution diagnostics (maintain_r1 only, never the reward)

`verify.evolution_report` runs two extra passes and hands everything to `evolution.report`:

| Run | Agent | Cases | Console | Where from |
|---|---|---|---|---|
| Q00 | A0 | R0 | R0 | saved by `build_r0` (`keep_a0`) |
| Q01 | A0 | R1 | R1 | extra run in `maintain_r1` |
| Q10 | A1 | R0 | R1 | extra run in `maintain_r1` |
| Q11 | A1 | R1 | R1 | the scored `maintain_r1` run itself |

### Axis 1 — placement (`diagnostics.placement`)

- **Reads:** the per-file diff from A0's workspace to A1's. `.py` and `.md` diffs go to the judge in chunks of at most `CHUNK_CHARS`; other changed files (the index) are listed by name. An empty diff is `no_change`.
- **Checks only what A1 absorbed:** a change is checked when A1 passes an R1 case of one of its `rules`; otherwise it is `not_absorbed`, so an A1 that changed nothing gets no credit for "should not happen" questions.
- **Judge questions** ask whether the behaviour is enforced in code or kept out of prose, not which file holds it. A question is yes when any chunk shows it, asked twice: answers that differ are `unstable`; a failed or clipped chunk is `incomplete`, never "no".
- **Scans** are regexes over the lines A1 added to markdown files (e.g. `$250` as prose), with no judge.
- **Counted:** `placed` covers changes with `grounds`, a quote from a builder-visible file (`app/framework/agent_building.md` or an R1 record) that says where it belongs; ungrounded changes compare with the reference agent only and are reported, not counted.

### Axis 2 — health (`diagnostics.py`)

- `run_health(log_dir)`: per-conversation means over one run's case records — seconds, model calls, prompt and completion tokens, credits, embedding calls, customer turns (`CaseCost`) — over all cases and over passing cases only (`passing`); compare runs on `passing`, since an agent that fails early looks cheap.
- `layer_sizes(workspace)`: lines per layer (`LAYERS`: system prompt, AGENTS.md, skills, subagents, hooks, tools) and bytes of everything else (mostly the index).
- `run_health` is reported for Q01 (A0) and Q10, Q11 (A1); `layer_sizes` for A0 and A1, so the cost of maintenance is visible.

### Axis 3 — uptake (`diagnostics.py`)

- `drift(q01, q11, rules)`: a case is **absorbed** when A0 failed it and A1 passes it. `absorbed_by_rule` gives "absorbed/covering" per R1 rule; `already_passed_by_a0` lists R1 cases the old agent passed anyway (C20's money-format read is expected there).
- `regression(q00, q10)`: a case **regressed** when A0 passed it and A1 fails it; unchanged R0 behaviour must survive.

## Running it locally

```sh
# one step, one or two cases, your workspace (needs OPENAI_API_KEY)
cd tasks/redwood-devsupport/tests && python -m grade.grade build_r0 path/to/workspace --only a0-12,a0-03

# the verifier's unit tests
uv run --python 3.12 --no-project --with pytest --with 'pydantic>=2.9,<3' --with 'openai>=1.100,<3' \
  pytest -q tests/redwood_devsupport/test_grade.py tests/redwood_devsupport/test_evolution.py tests/redwood_devsupport/test_cost.py

# lint, strict types, complexity, no getattr/setattr
scripts/check_redwood.sh
```
