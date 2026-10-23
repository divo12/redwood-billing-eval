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
