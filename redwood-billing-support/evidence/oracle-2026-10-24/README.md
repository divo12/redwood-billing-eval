# oracle-2026-10-24

Reward: `{"reward": 1.0, "q00": 1.0, "q11": 1.0, "drift": 1.0, "regression": 0.9667, "behaviour_robustness": 0.9778, "artifact_quality": 1.0}`

| Q | Passed | Failed cases |
|---|---|---|
| q00-A0-on-R0 | 30/30 | none |
| q01-A0-on-R1-baseline | 4/30 | [a1-01-T](conversations/q01-A0-on-R1-baseline/failed/a1-01-T.json), [a1-01](conversations/q01-A0-on-R1-baseline/failed/a1-01.json), [a1-02-T](conversations/q01-A0-on-R1-baseline/failed/a1-02-T.json), [a1-02](conversations/q01-A0-on-R1-baseline/failed/a1-02.json), [a1-03-T](conversations/q01-A0-on-R1-baseline/failed/a1-03-T.json), [a1-03](conversations/q01-A0-on-R1-baseline/failed/a1-03.json), [a1-04-A](conversations/q01-A0-on-R1-baseline/failed/a1-04-A.json), [a1-04-C](conversations/q01-A0-on-R1-baseline/failed/a1-04-C.json), [a1-04-M](conversations/q01-A0-on-R1-baseline/failed/a1-04-M.json), [a1-04](conversations/q01-A0-on-R1-baseline/failed/a1-04.json), [a1-05-M](conversations/q01-A0-on-R1-baseline/failed/a1-05-M.json), [a1-06-T](conversations/q01-A0-on-R1-baseline/failed/a1-06-T.json), [a1-06](conversations/q01-A0-on-R1-baseline/failed/a1-06.json), [a1-07-A](conversations/q01-A0-on-R1-baseline/failed/a1-07-A.json), [a1-07-C](conversations/q01-A0-on-R1-baseline/failed/a1-07-C.json), [a1-07-M](conversations/q01-A0-on-R1-baseline/failed/a1-07-M.json), [a1-07](conversations/q01-A0-on-R1-baseline/failed/a1-07.json), [a1-08-T](conversations/q01-A0-on-R1-baseline/failed/a1-08-T.json), [a1-08](conversations/q01-A0-on-R1-baseline/failed/a1-08.json), [a1-09-A](conversations/q01-A0-on-R1-baseline/failed/a1-09-A.json), [a1-09-M](conversations/q01-A0-on-R1-baseline/failed/a1-09-M.json), [a1-09](conversations/q01-A0-on-R1-baseline/failed/a1-09.json), [a1-10-A](conversations/q01-A0-on-R1-baseline/failed/a1-10-A.json), [a1-10-C](conversations/q01-A0-on-R1-baseline/failed/a1-10-C.json), [a1-10-M](conversations/q01-A0-on-R1-baseline/failed/a1-10-M.json), [a1-10](conversations/q01-A0-on-R1-baseline/failed/a1-10.json) |
| q10-A1-on-R0-regression | 29/30 | [a0-01-M](conversations/q10-A1-on-R0-regression/failed/a0-01-M.json) |
| q11-A1-on-R1 | 30/30 | none |

`builder/session.jsonl` is the builder's one session across both steps; `agents/` is what it
delivered; `step-checks/` is each step's verifier output; `scores.json` has the diagnostics.
See `../README.md` for how to read a conversation file.
