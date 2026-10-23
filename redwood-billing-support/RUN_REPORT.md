# Run report: redwood-billing-support

## Summary

| Agent | Q00 (A0 on R0) | Q10 (A1 on R0) | Q11 (A1 on R1) | Reward |
|---|---|---|---|---|
| Oracle (`evidence/oracle`) | 30/30 | 30/30 | 30/30 | **1.0** |
| Claude Opus 5.5, high, Claude Code (`evidence/opus-5-5-run5`) | 25/30 (0.833) | 26/30 (0.867) | 27/30 (0.90) | **0.0** |

Full `reward.json` (recomputed with the current `tests/grade/final.py` from the saved grading):

| Agent | reward | drift | regression | behaviour_robustness | artifact_quality |
|---|---|---|---|---|---|
| Oracle | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Opus 5.5 run 5 | 0.0 | 0.80 (12 of 15; that run graded Q01 on base cases only) | 0.96 (lost a0-08) | 1.0 | 1.0 |

`behaviour_robustness` 1.0 means no developer profile (missing info, ambiguous, conflict) broke a case its base
handled: Opus's failures are about rules, not about difficult developers.

Opus 5.5 built a working, well-structured agent (a retrieval index, three skills, a policy-researcher subagent,
hooks, wiring tests) and ran practice conversations in both steps. It still fails the gate on the build itself (Q00 < 0.9). In every recurring
failure, **the deciding record reached the support agent, but the builder never turned it into a decision rule
the agent acts on**, so the agent fell back to a cautious default: escalate, stay vague, or decline to give a
date. The oracle writes each rule down (its billing-investigation skill and `billing_rules.py`) and passes
everything.

## Setup actually used (disclosed)

- **Builder:** Harbor 0.22.0 `claude-code`, `claude-opus-5-5`, `reasoning_effort=high`, `max_budget_usd=30`, on a
  Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`). Run 5 spent $17.21 of the cap (Claude Code's own cost meter).
  Step 2 resumes the same Claude Code session (`resume_trajectory: true`; run 5's session `7a37d81f…` spans both
  steps in `evidence/opus-5-5-run5/builder/session.jsonl`), and the step verifier marks the run invalid if it
  does not.
- **Deployed support model and developer wording:** `gpt-6-luna` (reasoning high for support, low for wording) on
  Azure AI Foundry. This choice was about cost: a Claude support model would have made each 105-conversation
  grading far more expensive than the author's budget allows. The model is now a parameter (`SUPPORT_MODEL`,
  `USER_MODEL`, LiteLLM ids) that the builder-visible manifest and instructions name. Practice and grading use
  the same model, so the builder tunes against what is graded.
- **Since the reported runs:** the task was restructured for submission. Grading moved from a post-run script
  (`scripts/grade_snapshots.sh`) into `maintain_r1`'s verifier, and the support-model gateway moved from the
  OpenAI Responses API to LiteLLM chat completions, with the same model and effort. Case data, checks, the gate
  and the builder's instructions are otherwise unchanged. **Re-running the oracle and one Opus trial on this
  exact version is the next step**; until then the numbers above come from the version before these changes
  (task 1.1.0, both runs on 2026-10-23).

## The failure, in detail

The failing cases in run 5 (each read in full; linked from `evidence/opus-5-5-run5/README.md`):

| Case | Q | What the agent did | What was required, and where it is written |
|---|---|---|---|
| a0-01, a0-01-A/-C/-M | Q00 | Opened a Billing Engineering case for a token-count gap | Explain it: billing counts the full request with the model's own tokenizer. The current routing page says answer, don't hand off, unless request IDs still mismatch afterwards. |
| a0-09-T | Q00 | Searched "full request payload model-native tokenizer", heard the developer counted only user messages, still escalated | Same rule: explain that system prompt and tool definitions count |
| a0-01, -C, -M | Q10 | Same escalation on A1 | Same |
| a0-08 | Q10 | A1 dropped the $500 limit that still applies to pre-2027 invoices | R1's approvals matrix: $250 only for invoices on the 2027 price book, $500 for earlier ones |
| a1-03, a1-03-T | Q11 | Declined to give the payment-retry date | Past status cards fix it: retries complete 3 business days after the incident window ends |
| a1-07 | Q11 | Gave the March total but said it could not tell which requests were the runaway script's | Ask when it ran (the agent's own skill lists `time_of_issue`); the developer says March 17, and that day's two requests total 17,683,825 tokens |

**Classification** (the brief's terms):
- **Wrong hypothesis / not operationalised** (a0-01 family, a0-09-T, a1-03): retrieval worked, but the builder
  shipped "escalate when unsure" instead of the rule. With the a1-03 rule placed in the system prompt
  (`REDWOOD_REPLAY_HINT`, a diagnostic replay), the agent answers a1-03 correctly. So the knowledge was missing
  from the build, not unusable by the support model.
- **Brittle patch** (a0-08 on A1): the R1 update replaced the $500 limit instead of scoping it by price-book
  date, a regression on a behaviour the R0 build got right.
- **Incomplete inspection** (a1-07): it looked up six requests but never asked the one question that separates
  them.
- **Skipped verification:** practice was mostly the provided example scripts, plus two of the builder's own (an
  SLA goodwill claim and a card-processor failure). None covered a tokenizer gap, a retry date or the
  date-scoped credit limit, so practice could not catch these failures.

**Recurring.** The same failures show up across builds of earlier case sets (runs 1-4, same builder): a1-03 failed
in every graded build; a0-01 escalated in Q10 in all five runs and in Q00 in four (run 3 passed only because its
own tool bug blocked the handoff); A1 dropped the $500 scope in four of five. Run 3 failed for a different,
self-inflicted reason (a 200-character validator in its own handoff tool), which is what led to adding practice
conversations.

## Oracle

`steps/*/solution/solve.sh` installs a reference workspace (hybrid retrieval over embedded billing records, a
billing-investigation skill and `billing_rules.py` that state each rule, a policy subagent, hooks), builds its index with the same local embedder, and on R1 embeds only
the new and revised records. Oracle job `2026-10-23__20-38-54`: Q00, Q10 and Q11 30/30 each, reward 1.0,
artifact quality 1.0 on both steps. Every behaviour variant and twin passed, no case was invalid and the developer
leaked nothing. It runs through the same conversation loop, sandbox and console as the builder's agent, with no
shortcut.

## Fairness audit

Done over the oracle's and run 5's 105 graded conversations each, the builder trace, the image and the data:
- **Solvable from visible files.** Every rule cites builder-visible records per release (test-enforced). Every
  value the agent must say appears in a tool result, except a1-03's date, which the past status cards determine
  uniquely. The oracle passes 90/90.
- **The failures are the agent's.** For a0-01 and a0-09-T, no current record tells Support to escalate a
  tokenizer gap; the only contrary record is a superseded draft. For a0-08, the R1 matrix states both limits. For
  a1-07, the developer gives the date when asked.
- **Simulated developer.** Across 188 worded lines in run 5 there were 0 invented values, 0 team or rule names,
  0 answers to questions asked in chat and 0 leaks. Every ending matches the console (a transfer exactly when a
  case was escalated), and the clock showed the right date in every case.
- **No leakage.** The builder trace was audited: no hidden path, value or R1 record reached it before R1. `/tests`,
  seeds, overlays and the practice service are root-only. A grading-time leak (task folder mounted
  world-readable to graded agents) was found and closed; no saved workspace had used it. Identifier collisions
  that could satisfy a check by accident were removed (`req_pb402` vs "402"), with a test.
- **Not brittle.** Values match in any listed form after normalising case, commas, `$`, markdown and dashes. The DB
  check accepts every outcome the records allow (e.g. either duplicated line). Twins (a similar ask, the
  opposite answer) catch pattern-matching, and swapping twin golds fails.
- **No LLM judge.** A model only rewords scripted lines; every verdict is reproducible from the case log.

## Realism and economic value

Support agents grounded in company knowledge are among the most deployed LLM applications, and their cost is not
the first build but keeping them right as pricing, policy and data formats change. This task is that job: a
noisy multi-platform export, rules owned by Finance, Security and Support, drafts and superseded pages, a
breaking console change, and a six-month gap. A wrong answer here is a real cost (an unneeded engineering
ticket, a credit over the approval limit, a customer told the wrong retry date).

Cost per trial: one builder run (about $17 of Opus at Claude Code's meter) plus about 90 graded conversations
(about 117K input and 3K output tokens each). At published gpt-6-luna prices that is roughly $1-2 per grading.
Grading needs a provider rate limit near 500K tokens/min to finish in about 80 minutes.

## Limitations

- One target-model trial on the final case set (plus four builds on earlier sets showing the same failures).
  30 cases per release means one case moves a rate by 0.033; the gate allows one case of slack on regression.
- The support model is gpt-6-luna for cost. The failure is the builder's, and the replay hint shows the support
  model can act on a rule once it is written down, but results with a Claude support model are not reported.
- A full trial takes about 4.5 h and needs provider keys for the support model.
- The reported runs predate the restructure (see *Setup actually used*).

## Reproduce

```sh
harbor run -p ./redwood-billing-support -a oracle -o "$PWD/jobs"
harbor run -p ./redwood-billing-support -a claude-code -m claude-opus-5-5 --ak reasoning_effort=high -o "$PWD/jobs"
harbor view ./jobs
python redwood-billing-support/scripts/curate_evidence.py jobs/<job>/<trial> <label>   # refresh evidence/
```
