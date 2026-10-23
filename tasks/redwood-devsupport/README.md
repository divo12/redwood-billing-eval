# collinear-candidate/redwood-billing-support

A two-step, long-horizon Harbor task. A builder agent (B, the model under test, e.g. Claude Opus 5.5 in
Claude Code) builds a billing-support agent (A) for Redwood Inference from a corpus of company records
(R), then **maintains** it when the company's world changes. The question: does B evolve A in the right
places when policy, data formats and authority change underneath it?

See `RUN_REPORT.md` for the idea, fairness rationale, runs and limitations.

## Run

Secrets come from your shell (Harbor resolves `${VAR}` from the host). Embeddings run on a local
sentence-transformer (`sentence-transformers/all-MiniLM-L6-v2`) baked into the image, so no other key is needed:

| Secret | Used by | Hosts |
|---|---|---|
| `ANTHROPIC_API_KEY` | the builder (Claude Code) and the support agent A during grading (`claude-sonnet-5-5`, prompt-cached) | `api.anthropic.com` (+ Claude Code's own install/telemetry hosts) |

```sh
cd <repo root>
harbor run -p ./tasks/redwood-devsupport -a oracle -o "$PWD/jobs"                       # oracle: 1.0 on both steps
harbor run -p ./tasks/redwood-devsupport -a claude-code -m claude-opus-5-5 \
    --ak reasoning_effort=high -o "$PWD/jobs"                                           # the model under test
harbor run -y -c tasks/redwood-devsupport/run-config.claude.json -o "$PWD/jobs"        # same, as a config (resumes step 2)
harbor view ./jobs
```

Harbor 0.22.0 has no agent named `claude`: Claude Code is `claude-code`.

Then grade both saved workspaces (the run's reward is `<trial>/graded/reward.json`):

```sh
scripts/grade_snapshots.sh jobs/<job>/<trial>
```

## Shape

| Step | World | Builder does | Graded on |
|---|---|---|---|
| `build_r0` | R0 (today 2026-09-15) | builds A0 in `/app/workspace` | 10 R0 scenarios |
| `maintain_r1` | R1 = R0 + ΔR (today 2027-04-08) | continues the same session, turns A0 into A1 | 10 R1 scenarios, every one exercising a ΔR change |

Every conversation is with a **scripted developer** (no model): an opening, a fixed agenda, facts the
agent can get only through the `ask_developer` tool, and a fixed answer to `confirm_with_developer`.
Every check is code: billing end state, required console actions, required and forbidden values, and
consent order. The reward is a gate over Q00 (A0 on R0), Q10 (A1 on R0) and Q11 (A1 on R1);
`scores.json` adds drift, regression, health and sizes, unscored. See `tests/README.md`.

## Folders

| Path | What it is |
|---|---|
| `task.toml`, `run-config*.json` | Harbor task definition and run configs (builder model; oracle run). |
| `Task.md` | Design notes and validation history. |
| `data/` | The billing console's seed data: R0 and the R1 delta. |
| `environment/` | The container: pinned Dockerfile and `requirements.lock`, the `redwood_kit` runtime, the builder-visible workspace and docs, the R1 reveal script, and `corpus/` (R0, R1, NOTICE). |
| `steps/` | Per-step instructions, the step's test entry script, and the oracle solution. |
| `tests/` | The hidden verifier (per step) and the grader (after the run). |

## data/ — billing console seed data

The support agent reads and writes Redwood's billing system only through the
console in `redwood_kit/billing`. These files are that console's starting state.

| File | Release | Contents |
|---|---|---|
| `db.json` | R0 | Every table (accounts, invoices, usage, credit grants, credit notes, disputes, cases, …) as it stands on 2026-09-15. |
| `db_r1.json` | R1 only | **Additions only**: the new 2027 invoices and usage, the new `credit_ledger` table, and rows the R1 scenarios need. |

How they are loaded (`tests/run/run_agent.load_db`):

- **R0:** `db.json` alone. R1-only tables (such as `credit_ledger`) are hidden and rejected.
- **R1:** a fresh copy of `db.json` with `db_r1.json` merged on top, table by table. A row id that already exists in R0 is an error, so the delta can never silently change R0 data.

Each file holds only what exists by its release's "today" (R0 2026-09-15, R1 2027-04-08), so the console serves the loaded rows as they are.
Loading checks shape, references and the writable-table rules (`billing.validation`); the read-only tables' invariants are data facts checked once in `tests/redwood_devsupport/test_billing_data.py`.
The DB check in `tests/` replays expected actions on this same seed, so a change here changes what "correct" means — ground any new row in a visible R record.

## Flow

```
build_r0:    builder edits /app/workspace ──> tests/verify.py build_r0 (gates, freeze; A0 kept)
             environment/advance.py reveals R1 in /app/R, seals /tests
maintain_r1: builder resumes, edits A0 -> A1 ──> tests/verify.py maintain_r1 (gates, freeze; A1 kept)
after:       scripts/grade_snapshots.sh ──> Q00, Q01, Q10, Q11 ──> grade.final ──> reward.json, scores.json
```

## Checks

```sh
scripts/check_redwood.sh                        # ruff, strict mypy, xenon, no getattr/setattr
uv run ... pytest -q tests/redwood_devsupport   # see Task.md for the exact command
```
