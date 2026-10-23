# Evidence

One folder per reported run, all the same shape (written by `scripts/curate_evidence.py` from Harbor's `jobs/`):

| Run | Agent | Reward | Q00 | Q10 | Q11 | Q01 (baseline) |
|---|---|---|---|---|---|---|
| [`oracle-submitted/`](oracle-submitted/) | oracle, exactly the submitted code | 1.0 | 30/30 | 30/30 | 28/30 | 4/30 |
| [`oracle-final/`](oracle-final/) | oracle | 1.0 | 30/30 | 29/30 | 30/30 | 4/30 |
| [`oracle-2026-10-24/`](oracle-2026-10-24/) | oracle | 1.0 | 30/30 | 29/30 | 30/30 | 4/30 |
| [`oracle-2026-10-24-b/`](oracle-2026-10-24-b/) | oracle | 1.0 | 30/30 | 29/30 | 29/30 | 5/30 |
| [`oracle/`](oracle/) | oracle, previous version | 1.0 | 30/30 | 30/30 | 30/30 | 1/15 |
| [`opus-5-5-run9-openrouter/`](opus-5-5-run9-openrouter/) | Opus 5.5 via OpenRouter | 0.0 | 24/30 | 22/30 | 27/30 | 5/30 |
| [`opus-5-5-run8/`](opus-5-5-run8/) | Opus 5.5 | 0.0 | 25/30 | 26/30 | 29/30 | 5/30 |
| [`opus-5-5-run7/`](opus-5-5-run7/) | Opus 5.5 | 0.0 | 22/30 | 21/30 | 24/30 | 6/30 |
| [`opus-5-5-run6/`](opus-5-5-run6/) | Opus 5.5 | 0.0 | 25/30 | 23/30 | 26/30 | 8/30 |
| [`opus-5-5-run5/`](opus-5-5-run5/) | Opus 5.5, previous version | 0.0 | 25/30 | 26/30 | 27/30 | 0/15 |

Each run's `README.md` links every failed conversation. `oracle/` and `opus-5-5-run5/` predate the move of
grading into Harbor, and their Q01 graded base cases only (see `RUN_REPORT.md`, *Setup actually used*).

The folders hold each run as it was graded. A few failures in runs 5-8 come from verifier defects that the
fairness audit later fixed: "never said 500" on a0-08, and tokenizer explanations in other words. `RUN_REPORT.md`
reports both the graded and the rescored numbers, and its *Genuine failure modes* section excludes those
failures.

## How the reward is derived

**Two agents, two releases.**
- **A0** is the agent the builder delivers after `build_r0`, built from the company export R0.
- **A1** is the same agent after `maintain_r1`, once the export has grown to R1.
- Each release has 30 graded cases: 10 rule cases, 5 twins and 15 developer-behaviour variants.

**The four Q runs.** A "Q" is one agent graded on one release's 30 cases. `Qxy` means agent A*x* on the cases of
release R*y*:

| Q | Agent | Cases | Console it sees | What it answers | Used in |
|---|---|---|---|---|---|
| Q00 | A0 | the 30 R0 cases | R0 | Is the original build good? | the gate and `q00` |
| Q01 | A0 | the 30 R1 cases | R1 | How stale is the unmaintained build? This is the baseline | `drift` (not the gate) |
| Q10 | A1 | the 30 R0 cases | R1 record format, each conversation's own R0 records | Did maintenance break what still applies? | the gate and `regression` |
| Q11 | A1 | the 30 R1 cases | R1 | Does the maintained agent handle the new world? | the gate and `q11` |

**One case passes only if all of its code checks pass.** There is no LLM judge:
- `db`: the console's end state matches the gold result or an accepted alternative;
- `action`: the required console actions were taken;
- `communicate`: every required value was said;
- `never_say`: no forbidden value was said;
- `consent`: the developer said yes before any credit or dispute.

A case is **invalid**, and left out of every rate, when the support model's provider failed or the reworded
developer leaked a value the agent had to say. A Q's pass rate is passed cases divided by valid cases.

**The reward is a gate over Q00, Q10 and Q11** (`tests/grade/final.py`, `gate`):

```
kept   = Q10 >= Q00 - 0.1                       # maintenance lost at most 3 of 30 cases
reward = 1.0  if Q00 >= 0.9 and Q11 >= 0.9 and kept    # good build, maintained, nothing broken
         0.5  if Q00 >= 0.9 and kept                   # good build kept, but the R1 change not handled
         0.0  otherwise                                 # the build itself is below the bar, or maintenance broke it
```

**The other keys in `reward.json`** are reported beside the gate. They don't change it:
- **`q00`, `q11`:** the pass rates above.
- **`drift`:** of the R1 cases A0 fails in Q01, the share A1 passes in Q11. It measures how much of the change
  maintenance absorbed.
- **`regression`:** of the R0 cases A0 passes in Q00, the share A1 still passes in Q10. 1.0 means maintenance
  broke nothing.
- **`behaviour_robustness`:** over Q00, Q10 and Q11, for the variants (missing info, ambiguous, conflict) whose
  base case passed in the same Q, the share that passed too. A failing base already fails on the rule, so only
  these variants measure the developer's behaviour alone.
- **`artifact_quality`:** the share of `verify.py`'s deliverable checks that A1 passes: prompt size limits, a
  non-stub skill and subagent, an `@hook` guardrail, and `load_skill` and `delegate` reachable. A0 gets the same
  checks in `build_r0`, recorded in `step-checks/` but not in the final reward. A step that doesn't resume the
  builder's session is marked invalid instead of scored.

Any score with nothing to measure is 0.0 (no evidence). `scores.json` adds the four rates, invalid cases, twins,
information types, per-case drift and regression, run health and workspace sizes.

**Two worked examples:**
- **`oracle-submitted`:** Q00 1.0, Q10 1.0, Q11 0.933. Q00 and Q11 are both at least 0.9, and Q10 ≥ Q00 − 0.1,
  so **reward 1.0**.
- **`opus-5-5-run8`:** Q00 0.833 (25 of 30), which is below 0.9, so **reward 0.0**. Its maintenance was good
  (Q11 0.967, regression 1.0), but the original build failed the bar: its tokenizer escalations (`RUN_REPORT.md`,
  *Genuine failure modes*).

## Inside a run

```
README.md                    reward, pass rate per Q, and a link to every failed case
reward.json, scores.json     the reward and its unscored diagnostics (tests/README.md explains each key)
builder/
  session.jsonl              the builder's ONE session: Claude Code resumes it in step 2, so this file holds both steps
  1-build_r0.stream.jsonl    the raw stream Harbor recorded during step 1 (reasoning, tool calls, results)
  2-maintain_r1.stream.jsonl the same for step 2
agents/
  A0-after-build_r0/         the support agent the builder delivered after step 1 (source; indexes left out)
  A1-after-maintain_r1/      after step 2
step-checks/<step>/          each step's verifier: deliverable checks, session continuity, workspace diff
conversations/
  q00-A0-on-R0/              A0 on the 30 R0 cases                    (counts toward the reward)
  q10-A1-on-R0-regression/   A1 on the same R0 cases, R1 console      (counts: did maintenance break anything?)
  q11-A1-on-R1/              A1 on the 30 R1 cases                    (counts)
  q01-A0-on-R1-baseline/     A0 on the R1 cases: how stale the unmaintained agent is (baseline for drift;
                             all 30 since run 6)
    failed/  passed/         one JSON file per case
```

## Reading a conversation file

```json
{"id": "a1-07",
 "grade": {"passed": false, "checks": [{"kind": "communicate", "passed": false,
                                        "problem": "never said any of ['17683825', '17.68 million', ...]"}]},
 "termination": "customer_transfer",
 "transcript": [{"role": "assistant", "content": "Hi! How can I help you today?"},
                {"role": "user", "content": "Hi, a runaway script ..."},
                {"role": "assistant", "tool_calls": [...]}, {"role": "tool", "content": "..."}]}
```

`grade.checks` lists every check for the case (`db`, `action`, `communicate`, `never_say`, `consent`) with what
failed and why. `transcript` is the whole conversation, including every console call and its result, so each
verdict can be checked by hand. The case definitions (gold, required values) are in
`tests/data/billing_scenarios.json`.
