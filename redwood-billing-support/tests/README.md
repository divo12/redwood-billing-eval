# tests/ — the hidden verifier and grader

The builder never sees this folder. Grading runs in `maintain_r1`'s verifier, once both workspaces exist (A0 was
frozen at the end of `build_r0`), because the reward needs both agents at once.

| Stage | What runs | Writes |
|---|---|---|
| `build_r0` | `verify.py build_r0`: gates, workspace report, `freeze` (A0 → `/run/redwood-agent-R0`), artifact checks | `evidence.json`, a placeholder `reward.json` (`graded: false`) |
| `maintain_r1` | `verify.py maintain_r1` (same for A1), then `/tests` is made root-only and `grade.grade` runs Q00, Q10, Q11 (graded agents run as `nobody`), then `grade.final` | `reward.json` (the gate, the three rates, `artifact_quality`), `graded/scores.json`, `graded/q*/cases/` |
| Optional, after the run | `scripts/grade_snapshots.sh <trial>`: Q01 (diagnostic) or a regrade of invalid cases, in the sealed `redwood-grader` image | `<trial>/graded/` |

| Run | Agent | Cases | Console |
|---|---|---|---|
| Q00 | A0 | R0 | R0 |
| Q01 | A0 | R1 (base cases only: diagnostic) | R1 |
| Q10 | A1 | R0 | R1 |
| Q11 | A1 | R1 | R1 |

**Reward** (`grade.final.gate`): 1.0 when Q00 ≥ 0.9, Q11 ≥ 0.9 and Q10 ≥ Q00 − 0.1;
0.5 when only Q00 and the Q10 condition hold; otherwise 0.0.
**`scores.json`** (none of it changes the reward): `q` the four pass rates; `invalid` cases whose conversation says
nothing about the agent (the provider failed, or the developer gave an answer away), left out of the rates and
listed for regrading with `--only`; `info` pass rate by info_type and twin pairs (a pair counts only when both
halves pass); `behavior` pass rate per behaviour (missing_info, ambiguous, conflict) and of their bases,
robustness (variant rate / base rate) and each variant against its base; `drift` (R1 cases A1 absorbed);
`regression` (R0 cases A1 lost); `health` per Q (per-case seconds, model calls, tokens, embedding calls, developer
turns, over all and passing cases, and `ends`, terminations with agent errors split by kind); `sizes` per layer.

## File map

```
verify.py                one Harbor step: gates, freeze, artifact checks (no grading)
run/                     one conversation
├── run_agent.py         grading's side of a conversation: the hidden case, the in-process billing store, the
│                        evidence (`Result`), the case's clock; the loop itself is redwood_kit.conversation
├── customer.py          the scripted developer (re-exports redwood_kit.customer, shared with practice)
├── written_customer.py  USER_MODEL words each scripted line; cached per run for regrading
└── gateway.py           SUPPORT_MODEL / USER_MODEL calls through LiteLLM, embeddings on the local MiniLM
grade/
├── grade.py             the code-decided checks; `python -m grade.grade` grades one Q run
├── final.py             the reward gate and scores.json (invalid, info, behavior) from the four Q runs
└── scenarios.py         typed models of billing_scenarios.json
evolve/diagnostics.py    drift, regression, health, layer sizes
data/
├── billing_scenarios.json  30 R0 cases (`a0-*`) and 30 R1 cases (`a1-*`): 15 base (10 + 5 twins) and 15
│                           behaviour variants (`-M`, `-A`, `-C`) per release
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
3. A Q run's pass rate is the mean over its cases (`grade.summarize`); behaviour variants count like any case.

A provider failure marks only its own case invalid (`Result.invalid`), as does a developer wording that says a
value the agent must say before the agent does (`grade.developer_leak`). Invalid cases are left out of the pass
rates and listed in `scores.json`; regrade them with `--only`. Each conversation runs on its case's clock (brief
`today` plus `now`, UTC) through libfaketime.

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

### 4. Communicate check — `grade.check_communicate`

Only when the scenario has `communicate`.

- **Reads:** the agent's messages.
- **Fails when:** for some value, none of its listed forms appears (case, commas, `$`, markdown and typographic
  quotes and dashes ignored; no match inside a longer number).
- **Example:** a1-03 `[["2027-04-12", "April 12", ...]]`: the retry date worked out from past status cards.

### 5. Consent check — `grade.check_consent`

Only when the scenario's rules include BILL-CONFIRM.

- **Fails when:** a successful credit note or dispute insert came before any `yes` from `confirm_with_developer`.

### Checks we deliberately do not have

They would only repeat another check: the escalation team (DB check), ordinary write arguments (DB replay), required/forbidden writes (DB replay), the order of the lookup and the write (hyper-tau's ACTION does not grade order either).

## Running it

```sh
# a full run: the builder, then grading in maintain_r1's verifier (provider keys for SUPPORT_MODEL, see README)
harbor run -y -c redwood-billing-support/run-config.claude.json -o "$PWD/jobs"
scripts/grade_snapshots.sh jobs/<job>/<trial>        # optional regrade outside Harbor; QS="q01" for the diagnostic

# one Q run on any workspace, or a few cases
cd redwood-billing-support/tests && python -m grade.grade build_r0 path/to/workspace --only a0-03

# unit tests, then lint, strict types, complexity
TMPDIR=/tmp uv run --python 3.12 --no-project --with pytest --with 'pydantic>=2.9,<3' --with litellm==1.104.2 \
  --with 'fastembed==0.9.0' --with 'lancedb>=0.22,<0.30' --with 'numpy>=2,<3' \
  pytest -q -c dev/pyproject.toml --rootdir dev dev/tests
scripts/check_redwood.sh
```
