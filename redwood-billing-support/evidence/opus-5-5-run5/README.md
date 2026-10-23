# opus-5-5-run5

Reward: `{"reward": 0.0}`

| Q | Passed | Failed cases |
|---|---|---|
| q00-A0-on-R0 | 25/30 | [a0-01-A](conversations/q00-A0-on-R0/failed/a0-01-A.json), [a0-01-C](conversations/q00-A0-on-R0/failed/a0-01-C.json), [a0-01-M](conversations/q00-A0-on-R0/failed/a0-01-M.json), [a0-01](conversations/q00-A0-on-R0/failed/a0-01.json), [a0-09-T](conversations/q00-A0-on-R0/failed/a0-09-T.json) |
| q01-A0-on-R1-diagnostic | 0/15 | [a1-01-T](conversations/q01-A0-on-R1-diagnostic/failed/a1-01-T.json), [a1-01](conversations/q01-A0-on-R1-diagnostic/failed/a1-01.json), [a1-02-T](conversations/q01-A0-on-R1-diagnostic/failed/a1-02-T.json), [a1-02](conversations/q01-A0-on-R1-diagnostic/failed/a1-02.json), [a1-03-T](conversations/q01-A0-on-R1-diagnostic/failed/a1-03-T.json), [a1-03](conversations/q01-A0-on-R1-diagnostic/failed/a1-03.json), [a1-04](conversations/q01-A0-on-R1-diagnostic/failed/a1-04.json), [a1-05](conversations/q01-A0-on-R1-diagnostic/failed/a1-05.json), [a1-06-T](conversations/q01-A0-on-R1-diagnostic/failed/a1-06-T.json), [a1-06](conversations/q01-A0-on-R1-diagnostic/failed/a1-06.json), [a1-07](conversations/q01-A0-on-R1-diagnostic/failed/a1-07.json), [a1-08-T](conversations/q01-A0-on-R1-diagnostic/failed/a1-08-T.json), [a1-08](conversations/q01-A0-on-R1-diagnostic/failed/a1-08.json), [a1-09](conversations/q01-A0-on-R1-diagnostic/failed/a1-09.json), [a1-10](conversations/q01-A0-on-R1-diagnostic/failed/a1-10.json) |
| q10-A1-on-R0-regression | 26/30 | [a0-01-C](conversations/q10-A1-on-R0-regression/failed/a0-01-C.json), [a0-01-M](conversations/q10-A1-on-R0-regression/failed/a0-01-M.json), [a0-01](conversations/q10-A1-on-R0-regression/failed/a0-01.json), [a0-08](conversations/q10-A1-on-R0-regression/failed/a0-08.json) |
| q11-A1-on-R1 | 27/30 | [a1-03-T](conversations/q11-A1-on-R1/failed/a1-03-T.json), [a1-03](conversations/q11-A1-on-R1/failed/a1-03.json), [a1-07](conversations/q11-A1-on-R1/failed/a1-07.json) |

`builder/session.jsonl` is the builder's one session across both steps; `agents/` is what it
delivered; `step-checks/` is each step's verifier output; `scores.json` has the diagnostics.
See `../README.md` for how to read a conversation file.
