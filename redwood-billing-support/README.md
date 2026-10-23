# redwood-billing-support

A two-step, long-horizon Harbor task about **building and then maintaining an LLM agent**. The model under test
(the builder, Claude Opus 5.5 in Claude Code) is the engineer who owns Redwood Inference's billing-support agent:

1. **`build_r0`** (2 h limit): read a 3,529-record company export (Slack, Gmail, Jira, Linear, Confluence, Drive,
   HubSpot, Fireflies, GitHub) that mixes current policy with drafts, superseded pages and unrelated noise; decide
   what knowledge to ship; build a retrieval index, tools, a skill, a subagent and a guardrail hook; test it.
2. **`maintain_r1`** (1 h limit, same session): six months later the export grows by 3,561 records, three pages are
   revised *in place*, and the billing console changes format. Update the agent without breaking what it did.

The delivered agent (on the deployed support model) then holds **90 held-out conversations** with a scripted
developer against a stateful billing console. Every check is code: console end state, required console actions,
values the agent must and must not say, and consent before credits. No LLM judge.

Why it is hard: no single file holds the answers. The rules are scattered across owners and platforms, decoys
share their spaces, and R1 changes some rules by date (a $500 credit limit becomes $250 only for invoices rated
on the 2027 price book). The builder must find the rules, turn them into behaviour the agent acts on, and then
change them in exactly the right places. See `RUN_REPORT.md` for how Opus 5.5 fails.

## Where the required files are

This is a two-step Harbor task, so Harbor's multi-step layout places the per-step files under `steps/<step>/`
(Harbor ignores top-level copies when `task.toml` declares `[[steps]]`).

| Brief's requirement | In this task |
|---|---|
| `instruction.md` | `steps/build_r0/instruction.md` and `steps/maintain_r1/instruction.md` |
| `task.toml` | `task.toml` (metadata, timeouts, resources, network policy, authorship, category, both steps) |
| `environment/Dockerfile` / `docker-compose.yaml` | `environment/Dockerfile` (base image pinned by digest, Python pinned in `requirements.lock`) and `environment/docker-compose.yaml` |
| `tests/test.sh` writing `reward.json` | `steps/build_r0/tests/test.sh` and `steps/maintain_r1/tests/test.sh`; the latter writes the trial's `/logs/verifier/reward.json` |
| `tests/test_*.py` or grader files | `tests/` (the verifier and grader: `verify.py`, `run/`, `grade/`, `data/`) and the task's own unit tests in `dev/tests/` |
| `solution/solve.sh` | `steps/build_r0/solution/solve.sh` and `steps/maintain_r1/solution/solve.sh` |
| Seed files | `environment/corpus/` (R0, R1), `data/` (billing console seeds), `environment/app/` (starter agent and builder docs) |
| `README.md` / `RUN_REPORT.md` | this file, `RUN_REPORT.md`, `DESIGN_DOC.md` |

## Run

Secrets come from your shell (Harbor resolves `${VAR}` from the host). Keep them in a gitignored `.env` and load it
with `set -a; . ./.env; set +a`.

| Variable | Used for |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) | the builder (Claude Code, `claude-opus-5-5`) |
| `SUPPORT_MODEL` | the deployed support model, a [LiteLLM](https://docs.litellm.ai) id; default `openai/gpt-6-luna` |
| `USER_MODEL` | the model that words the scripted developer's lines; default `openai/gpt-6-luna` |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | provider credentials for `openai/...` models (an Azure `/openai/v1` URL works) |
| `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | instead, for `anthropic/...` or `openrouter/...` models |
| `GRADE_WORKERS` | conversations graded in parallel (default 4, which needs about 1M tokens per minute of rate limit; lower it for a smaller limit) |

`SUPPORT_MODEL` is fixed per run. The image writes it into the builder-visible
`framework/deployment_manifest.json` and the starter agent, and the instructions name the parameter. Practice and
grading both serve it. Embeddings are a local sentence-transformer baked into the image.

```sh
# from the repo root, with the keys loaded: set -a; . ./.env; set +a
harbor run -p ./redwood-billing-support -a oracle -o "$PWD/jobs"                         # oracle: reward 1.0
harbor run -p ./redwood-billing-support -a claude-code -m claude-opus-5-5 --resume-trajectory \
    --ak reasoning_effort=high --ak max_budget_usd=30 -o "$PWD/jobs"                     # the model under test
harbor run -y -c redwood-billing-support/run-config.claude.json -o "$PWD/jobs"           # same, as a config
harbor view ./jobs
```

Harbor 0.22.0 names the Claude Code agent `claude-code` (there is no agent called `claude`). `--resume-trajectory` is
required: step 2 must continue the builder's step-1 session, and the verifier marks a run that starts afresh as
invalid (Harbor's default is a fresh session per step). Measured over Opus
runs 5-9, building takes about 20 min and maintaining about 12 min, well inside the 2 h and 1 h limits. Grading
inside `maintain_r1`'s verifier takes 39-48 min, so a full Opus trial takes 70-78 min. An oracle trial takes 26-39
min. Grading makes about 120 × 7
support-model calls; with gpt-6-luna that needs about 1M tokens per minute of rate limit at 4 workers (500K at 2).

## Reward

`maintain_r1`'s verifier writes `/logs/verifier/reward.json`, and `multi_step_reward_strategy = "final"` makes it
the trial's reward. Every value is a number from 0 to 1, higher is better:

```json
{"reward": 0.0, "q00": 0.833, "q11": 0.9, "drift": 0.8, "regression": 0.96, "behaviour_robustness": 1.0, "artifact_quality": 1.0}
```
(Opus 5.5 run 5. The oracle on the submitted code: `{"reward": 1.0, "q00": 1.0, "q11": 0.9333, "drift": 0.9231, "regression": 1.0, "behaviour_robustness": 0.9556, "artifact_quality": 1.0}`.)

| Key | What it measures |
|---|---|
| `reward` | the gate: 1.0 if Q00 ≥ 0.9, Q11 ≥ 0.9 and Q10 ≥ Q00 − 0.1; 0.5 if the build is good and kept but R1 fails; else 0 |
| `q00`, `q11` | pass rates of the build on R0 and of the maintained agent on R1 (Q01 and Q10 feed `drift` and `regression`; all four rates are in `scores.json`) |
| `drift` | of the 30 R1 cases the unmaintained A0 fails (Q01), the share the maintained A1 passes (Q11): how much of the change maintenance absorbed |
| `regression` | of the R0 cases A0 passed (Q00), the share A1 still passes (Q10): 1.0 means maintenance broke nothing |
| `behaviour_robustness` | over Q00, Q10 and Q11, of the developer-profile variants (missing info, ambiguous, conflict) whose base case passed, the share that passed too; per profile in `scores.json` |
| `artifact_quality` | code checks on the deliverables: prompt size limits, a real skill, subagent and `@hook`, all reachable |

How these keys map to the brief's example:
- `overall` → `reward`;
- `functional_correctness` → `q00` and `q11`;
- `constraint_satisfaction` → `regression`, which checks that maintenance keeps what still applies;
- `robustness` → `behaviour_robustness`;
- `artifact_quality` → `artifact_quality`.

`drift` has no counterpart in the brief, because the brief does not measure evolution.

The Q runs: Q00 = A0 (the R0 build) on 30 R0 conversations; Q01 = A0 on the 30 R1 conversations (the baseline for drift); Q10 = A1 (the
maintained agent) on the 30 R0 conversations, R1 console; Q11 = A1 on 30 R1 conversations. A score with nothing to
measure is 0.0 (no evidence). `graded/scores.json` beside it adds the pass rates and unscored diagnostics: invalid
cases, pass rate by information type, twin pairs, per-case drift and regression, run health and workspace sizes.

## Verifier design

- **Conversations, not trajectory judging.** Each case is a scripted developer: an opening, an agenda, facts given
  only through the `ask_developer` tool, and a fixed consent answer. A model only rewords each line, and a wording
  that drops a number falls back to the script. The support agent runs in a sandbox (`nobody`, read-only frozen
  workspace, no network, 4 GiB) on the conversation's own clock (libfaketime).
- **Checks per case, all code.** `db`: the console's writable tables equal a gold insert/update trace or an
  accepted alternative. `action`: required lookups and writes happened. `communicate`: required values were said,
  in any listed form. `never_say`: the stale answer was not given. `consent`: credits only after a yes.
- **60 cases per run, 120 conversations per grading** (each release's 30 cases on two agents). Per release there are 10 rule cases, 5 twins (a similar ask
  whose correct answer differs) and 15 behaviour variants (the developer omits facts, is ambiguous, or asserts
  something false). Every rule cites the builder-visible records that state it (`tests/data/provenance.json`,
  test-enforced).
- **Invalid, not failed.** A provider error or a developer wording that leaks an answer marks the case invalid. It
  is excluded from the rates and listed for regrading.
- **Sealed.** `/tests` arrives only at verification and is made root-only before any graded agent runs. Hidden
  data, overlays and seeds are root-only in the image. During the build only the `practice-model` container holds
  provider keys.

## Layout

| Path | What it is |
|---|---|
| `DESIGN_DOC.md` | the reviewer's design doc: lineage, capability, designed traps, fairness audit, calibration, caveats, economics |
| `Task.md` | the exhaustive spec: every rule, case, check and decision |
| `task.toml`, `run-config*.json` | Harbor task and run configs |
| `steps/<step>/` | `instruction.md`, `tests/test.sh` (the verifier entry) and `solution/solve.sh` (the oracle) per step |
| `environment/` | pinned Dockerfile, compose file (main + practice-model service), `redwood_kit` runtime, builder docs, `corpus/` (R0, R1, NOTICE), the R1 reveal script |
| `data/` | billing console seeds: `db.json` (R0) and `db_r1.json` (R1 additions only) |
| `tests/` | the hidden verifier: `verify.py` (deliverable checks), `run/` (conversation loop, LiteLLM gateway, scripted developer), `grade/` (cases, checks, gate) |
| `scripts/` | `grade_snapshots.sh` (regrade a saved run outside Harbor), `curate_evidence.py`, `check_redwood.sh` |
| `dev/` | the task's own unit tests (178) and lint config |
| `authoring/` | the skills used to write the planted records, and the RAG research notes behind `architectures.md` |
| `evidence/` | curated traces and scores of the reported runs |

## Provenance and licences

- **Created for this task:** the scenario (Redwood Inference's billing support), every billing rule and its
  planted records (support-process, console, pricing, status-page, charter, Finance and decoy records, each cited
  in `tests/data/provenance.json`), the billing console and its data, the 60 conversations, the runtime kit,
  the verifier, the oracle and all docs.
- **External:** the background corpus is EnterpriseRAG-Bench's generated company data (MIT, DanswerAI; commit
  pinned in `environment/corpus/NOTICE`), split into R0/R1 with the task's records planted in. The embedding
  model is `sentence-transformers/all-MiniLM-L6-v2` (Apache-2.0). Python libraries are pinned in
  `environment/requirements.lock`. No benchmark task, issue, CTF or prior Collinear task was ported.

## Checks

```sh
scripts/check_redwood.sh     # ruff, strict mypy, xenon complexity
TMPDIR=/tmp uv run --python 3.12 --no-project --with pytest --with 'pydantic>=2.9,<3' --with litellm==1.104.2 \
  --with fastembed==0.9.0 --with lancedb --with numpy pytest -q -c dev/pyproject.toml --rootdir dev dev/tests
```
