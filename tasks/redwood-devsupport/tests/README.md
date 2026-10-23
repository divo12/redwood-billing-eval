# tests/ — the hidden verifier and grader

The builder never sees this folder. Grading happens **after** the Harbor run, on the
two workspaces Harbor saved, because the reward needs both agents at once.

| Stage | What runs | Writes |
|---|---|---|
| Each Harbor step | `verify.py build_r0 \| maintain_r1`: gates, workspace report, `freeze`, artifact checks. No conversations. | `evidence.json`, and a `reward.json` with `graded: false` |
| After the run | `scripts/grade_snapshots.sh <trial>`: `grade.grade` runs Q00, Q01, Q10, Q11, then `grade.final` | `<trial>/graded/reward.json`, `scores.json` |

| Run | Agent | Cases | Console |
|---|---|---|---|
| Q00 | A0 | R0 | R0 |
| Q01 | A0 | R1 | R1 |
| Q10 | A1 | R0 | R1 |
| Q11 | A1 | R1 | R1 |

**Reward** (`grade.final.gate`): 1.0 when Q00 ≥ 0.9, Q11 ≥ 0.9 and Q10 ≥ Q00 − 0.1;
0.5 when only Q00 and the Q10 condition hold; otherwise 0.0.
**`scores.json`**: the four pass rates, drift (R1 cases A1 absorbed), regression (R0 cases A1
lost), health (per-case seconds, model calls, tokens, embedding calls, developer turns, over
all and passing cases) and per-layer workspace sizes. None of it changes the reward.

## File map

```
verify.py                one Harbor step: gates, freeze, artifact checks (no grading)
run/                     one conversation
├── run_agent.py         sandboxed agent + billing store + the conversation loop
├── customer.py          the scripted developer: opening, agenda, facts for ask_developer, the confirm answer
├── gateway.py           the agent's model calls (Anthropic) and embeddings (local MiniLM)
└── openai_gateway.py    REDWOOD_SUPPORT_OVERRIDE=openai:<model>: serve the agent from an OpenAI/Azure model
grade/
├── grade.py             the code-decided checks; `python -m grade.grade` grades one Q run
├── final.py             the reward gate and scores.json from the four Q runs
└── scenarios.py         typed models of billing_scenarios.json
evolve/diagnostics.py    drift, regression, health, layer sizes
data/
├── billing_scenarios.json  10 R0 cases (`a0-*`) and 10 R1 cases (`a1-*`)
├── delta_r1.json        the R1 changes and the builder-visible quotes that ground them
└── provenance.json      which visible R record justifies each rule (`BILL-*`)
```

## One Harbor step (`verify.py`)

1. `check_environment` / `check_session`: the release on the box matches the step, `/app/R` is
   unreadable by the agent user, and the builder used one session continued across steps.
   A failure is an **invalid run** (exit 2), never a score.
2. `manifest` + `workspace_report`: which workspace files changed since the last step, and sizes.
3. `freeze`: a read-only, link-free copy. A missing `agent.py`/`tools.py` is an `artifact_error`.
4. `artifact_checks`: size limits, a non-stub skill, subagent and `@hook`, and that `load_skill`
   and `delegate` are exposed. Reported as `artifact_quality`.

## One scenario (`grade.grade`)

`run_agent.run_case` produces a `Result`: the messages,
the billing store's final state and its call log, plus model/embedding usage.

Then:

1. If the conversation **did not finish** (agent error, step limit, developer ran out of script),
   the scenario fails. Every billing check is marked failed with
   "the conversation did not finish".
2. Otherwise every check below runs. The scenario **passes when every check passes**.
3. A Q run's pass rate is the mean over *situations* (a scenario and its rewordings count once), `grade.summarize`.

Simulator failures are retried (`CASE_ATTEMPTS`) and then
raise: an invalid run, not a zero.

## The scenario checks

Each check returns one `CheckResult` (`grade.py`): `kind`, `passed`, and `problem` (why it failed, in words).

### 1. Never-say check — `grade.check_never_say`

Only when the scenario has `never_say`.

- **Reads:** the agent's messages (never the developer's).
- **Fails when:** any listed form of any value appears, with the same matching as `communicate` (case, commas and `$` ignored; no match inside a longer number).
- **Examples:** a1-09 `[["4111"], ["1111"]]`: the agent must not read back any group of the card number the developer pasted. a1-05 `[["20500000", "20.5 million"]]`: the UTC-month total a frozen R0 helper would quote.

### 2. DB check — `grade.check_db`

Only when the scenario has `db`.

- **Reads:** the billing store's final state.
- **Compares:** a *projection* of it (`billing.store.PROJECTION`: the fields that matter) against the projection of `db.gold` replayed on a fresh seed of the same release, and against each `db.alternatives` replay.
  - `credit_notes`: account, invoice, lines, amount, reason
  - `disputes`: account, invoice, lines, reason, request ids, status
  - `cases`: account, team, status, the linked dispute
  (stored rows keep `account_id` internally; on the R1 console the writable tables call it `billing_account_id`)
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
  - most scenarios: `{"op": "query:accounts", "equals": {"account_id": "windrose-analytics"}}`
  - a0-05: `{"op": "write:cases", "equals": {"status": "escalated"}, "contains": ["INV-2026-0831-301"]}`
- **Why it exists:** the DB projection ignores reads, `summary`/`details` and free text.

### Checks we deliberately do not have

They would only repeat another check: the escalation team (DB check), ordinary write arguments (DB replay), required/forbidden writes (DB replay), the order of the lookup and the write (hyper-tau's ACTION does not grade order either).

## Running it

```sh
# a full run: the builder, then grading (needs ANTHROPIC_API_KEY, or REDWOOD_SUPPORT_OVERRIDE)
harbor run -y -c tasks/redwood-devsupport/run-config.claude.json -o "$PWD/jobs"
scripts/grade_snapshots.sh jobs/<job>/<trial>        # QS="q00 q11" for some, WORKERS=1 under a rate limit

# one Q run on any workspace, or a few cases
cd tasks/redwood-devsupport/tests && python -m grade.grade build_r0 path/to/workspace --only a0-03

# unit tests, then lint, strict types, complexity
uv run --python 3.12 --no-project --with pytest --with httpx --with 'pydantic>=2.9,<3' --with 'openai>=1.100,<3' \
  --with 'anthropic>=0.60,<1' --with 'fastembed==0.9.0' --with 'lancedb>=0.22,<0.30' --with 'numpy>=2,<3' \
  pytest -q tests/redwood_devsupport
scripts/check_redwood.sh
```
