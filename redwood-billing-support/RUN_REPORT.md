# Run report: redwood-billing-support

## Summary

`claude-opus-5-5` at `reasoning_effort=high` (Claude Code) scores **reward 0 in 5/5 runs** (four on a Claude
subscription, one through OpenRouter); the oracle scores **1.0 in 5/5**.

**In every run, the build breaks a published rule it retrieved.** When a developer reports more billed tokens than
they counted and asks "can someone check the meter?", the routing page says *"Answer, don't hand off: how billed
tokens are counted (model-native tokenizer, full request payload) … explain it and stop there, even if the customer
calls it a bug."* Opus's R0 agent escalates these cases to Billing Engineering in all five runs (a0-01 and its
variants, a0-09-T), which alone keeps the build below the 0.9 bar (Q00 0.77-0.86 after every correction).

**When the world changes, a change inside an existing page is only half-adopted (3 of 5 runs).** R1 revises the
failed-payments page in place: a payment that fails inside a processor incident now needs **no case**, and its retry
date follows from past status cards (April 12). Run 5's maintained agent still followed the superseded procedure and
opened a case; runs 6 and 9 dropped the case but told the developer **April 10** (run 9 had even written a practice
script for this exact situation, and checked only that a date came back). In the same three runs the maintained
agent lost the $500 limit that the revised approvals matrix keeps for older invoices. Runs 7 and 8 maintained these
correctly, and run 8's maintenance even repaired the build's tokenizer escalations. The oracle passes all of these
cases in every run.

The failure survives the fairness audit below: rescored with every case or verifier defect it found removed, all
five runs still score 0.

| Agent | Run | reward | q00 | q11 | drift | regression | behaviour_robustness | artifact_quality |
|---|---|---|---|---|---|---|---|---|
| Oracle | `evidence/oracle-final` (submitted version) | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24` | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24-b` | **1.0** | 1.0 | 0.967 | 0.96 | 0.967 | 0.978 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run9-openrouter` (via OpenRouter) | **0.0** | 0.80 | 0.90 | 0.88 | 0.833 | 0.846 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run8` (submitted version) | **0.0** | 0.833 | 0.967 | 0.96 | 1.0 | 0.974 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run6` | **0.0** | 0.833 | 0.867 | 0.818 | 0.84 | 0.756 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run7` | **0.0** | 0.733 | 0.80 | 0.75 | 0.955 | 0.889 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run5` (earlier version) | **0.0** | 0.833 | 0.90 | 0.80 | 0.96 | 1.0 | 1.0 |

Two more oracle runs (`evidence/oracle`, an earlier version; and one graded in Harbor on 2026-10-23) also scored
1.0: five of five. Every case Opus fails, the oracle passes in at least 4 of 5 runs.

**The failure in one line:** the deciding records reach the support agent, but the builder never turns them into
behaviour the agent reliably acts on, and when the world changes it replaces rules instead of scoping them. The
agent falls back to a cautious default (escalate, hedge, or answer a different question). The oracle writes each
rule down (its billing-investigation skill and `billing_rules.py`) and passes.

**Why the failure is the builder's and not the support model's.** Both use the same support model (gpt-6-luna):
the oracle's build passes with it in every run. Writing the missing a1-03 rule into the prompt
(`REDWOOD_REPLAY_HINT`, a diagnostic replay) makes the support agent answer a1-03 correctly. The model can apply
a rule; Opus's builds did not give it the rule.

## Setup actually used (disclosed)

- **Builder:** Harbor 0.22.0 `claude-code`, `claude-opus-5-5`, `reasoning_effort=high`, `max_budget_usd=30`, on a
  Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`). Cost per run by Claude Code's meter: run 5 $17.21, run 6 $14.82
  (build $5.49, maintain $9.33), run 7 $16.63 (build $6.17, maintain $10.46). Step 2 resumes the same Claude Code
  session (`resume_trajectory: true`; each run's `builder/session.jsonl` spans both steps), and the step verifier
  marks the run invalid if it does not. Run 9 used the same harness and model (`anthropic/claude-opus-5.5`,
  verified in its session header) through OpenRouter's Anthropic-compatible endpoint (`ANTHROPIC_BASE_URL`);
  OpenRouter billed $10.40 for both steps. Its grading was cut off by a full disk on the host; both delivered
  agents and both traces had been saved, so it was regraded from them with the same verifier, at no builder cost.
- **Deployed support model and developer wording:** `gpt-6-luna` on Azure AI Foundry (reasoning high for the
  support agent, low for wording), chosen for cost. It is a parameter (`SUPPORT_MODEL`, `USER_MODEL`, LiteLLM ids)
  that the builder-visible manifest and instructions name. Practice and grading use the same model.
- **Versions.** Runs 6, 7 and the two `oracle-2026-10-24*` runs use the submitted version: grading inside
  `maintain_r1`'s verifier, LiteLLM, Q01 on all 30 R1 cases. Run 5 and `evidence/oracle` are the previous version
  (graded after the job by `scripts/grade_snapshots.sh`, OpenAI Responses API, Q01 on base cases only); same case
  set, checks and gate.

## The failure, in detail

Failures across the five Opus runs, grouped by what went wrong (each conversation is linked from its run's
`evidence/<run>/README.md`):

| Pattern | Cases | Runs | What the agent did | What the records say |
|---|---|---|---|---|
| **No restraint** | a0-01 and its variants, a0-09-T | 5, 6, 7, 8, 9 | Explained part of the token gap, then escalated "check the meter" to Billing Engineering, or hedged that it could not tell | The routing page: answer, don't hand off, when the published pages explain it (model-native tokenizer, full request payload), "even if the customer calls it a bug". The Billing Engineering row needs request IDs that still mismatch; the developer has none. |
| **Replaced instead of scoped** | a0-08 (run 5), a0-07-C and a0-08-T (run 6), a0-07-A and a0-07-C (run 9) | 5, 6, 9 | A1 could not apply the $500 limit to a 2026 invoice: escalated a creditable duplicate, or credited then escalated | R1's approvals matrix: $250 only for invoices on the 2027 price book, $500 still for earlier ones |
| **Rule never derived** | a1-03, a1-03-T | 5, 6, 9 | Declined to give the payment-retry date, or counted calendar days (April 10) | Four past status cards fix it: retries complete 3 business days after the incident window ends (runs 7 and 8 got it) |
| **Never asked** | a1-07 and its variants; a0-02-A, a1-04-A | 5, 6, 7, 8, 9 | Reported a total without asking when the runaway script ran, or picked one reading of an ambiguous ask | The developer gives the date (or the line) through the form when asked; that day's two requests total 17,683,825 tokens |
| **Wrong route** | a0-05-T, a1-04-A, a0-04-T | 7, 8, 9 | Asked in chat whether a budget complaint was a relief request; sent an export mismatch to Billing Engineering; sent a self-serve customer without a contract to the account team | Routing: relief on correct charges goes to Finance; an invoice-vs-export mismatch to Billing Ops; an account-team case only for an SLA claim under a contract |
| **Untested guardrail** | a0-08-T | 9 | Its own credit hook required citing a retrieved passage id; it cited one it had not retrieved, the hook blocked a valid $340 credit, and the agent told the developer the console had rejected it | The $500 limit allows the credit; nothing in the task blocks it |

**Classification** (the brief's terms):
- **Wrong hypothesis / not operationalised** (no restraint; rule never derived): retrieval worked (the agents quote
  the rule's own words), but the build shipped "escalate when unsure" instead of the rule.
- **Brittle patch** (replaced instead of scoped): the R1 update overwrote the $500 limit instead of scoping it by
  date, breaking behaviour the R0 build got right. `regression` measures it.
- **Incomplete inspection** (never asked; wrong route): the agent stopped at the first plausible reading.
- **Skipped verification:** practice was mostly the provided example scripts plus a few of the builder's own (run
  5: an SLA claim and a card-processor failure). None touched a tokenizer gap or a date-scoped limit. Run 9 did write
  a retry-date practice script, but checked only that a date came back, not that it was right; and it never ran a
  credit through its own guardrail hook, which then blocked a valid credit (a0-08-T).

**Recurring.** The restraint failure appears in every Opus run (and in runs 1-4 on earlier case sets); "never
asked" in every run; the scoped-limit regression in three of five; the retry-date inference in three of five (and in
every earlier build).

## Verifier design

`steps/maintain_r1/tests/test.sh` grades both agents after the builder's last step (full design in `DESIGN_DOC.md`
section 5):
- **Deliverable checks** on each workspace (`artifact_quality`): prompt size limits, a real skill, subagent and
  `@hook`, all reachable, and the builder's session resumed across steps.
- **120 conversations**: A0 and A1, each on the 30 R0 and the 30 R1 cases (Q00, Q01, Q10, Q11). The developer is a
  script in code (facts only through the `ask_developer` form, a fixed consent answer); a model only rewords its
  lines. The agent runs sandboxed as `nobody` on its frozen workspace, on the conversation's own date.
- **Five code checks per conversation, no LLM judge**: the console's end state against the gold trace (`db`),
  required console actions (`action`), required values said (`communicate`), forbidden values not said
  (`never_say`), and consent before any credit or dispute (`consent`).
- **Provider failures are invalid, not failed**, and are regradable.
- **The verifier is itself tested** (183 tests): a do-nothing agent fails all 60 cases, each twin's gold fails its
  pair, and each R1 case fails the write its old R0 rule would make.

## Oracle

`steps/*/solution/solve.sh` installs a reference workspace and runs it through exactly the same conversation loop,
sandbox, console, support model and checks as the builder's agent, with no shortcut (full description in
`DESIGN_DOC.md` section 6). Its R0 build has hybrid retrieval, a `billing-investigation` skill, a `policy-researcher`
subagent, hooks, and `billing_rules.py`; its R1 maintenance changes only what R1 changed, notably scoping the
duplicate-credit limit by invoice date (`duplicate_credit_limit_cents(period_start)`), and embeds only the new and
revised records. Five runs, reward 1.0 each (the last on exactly the submitted version); it misses at most a case or
two per run to the support model's sampling, a different one each time.

## Fairness audit

Every failing conversation of Opus runs 5-9 was read against the records that state its rule. By the brief's
criteria:

**Solvable.** The oracle scores reward 1.0 in 5/5 runs, the last on exactly the submitted version, through the
same conversation loop, console and support model. Every case Opus fails, the oracle passes in at least 4 of 5
runs. Every rule cites builder-visible records per release (test-enforced).

**Unambiguous.** The instructions state the deliverable, interfaces, limits and success measure, and leak no rule
value, team, threshold or answer (checked by scan). One case was ambiguous and was rewritten: a0-02-C's "conflict"
fit a routing row, and two agents defensibly routed it (now a false policy claim the retries page refutes; the oracle
passes it 3/3). Cases that are ambiguous by design (missing-info and ambiguous variants) are resolved by the
developer's form, which the contract tells the agent to use: a0-05-T fails only when the agent asks in chat instead.

**Substantive.** The remaining failures are capability gaps: a rule retrieved but not followed (tokenizer
escalations, 5/5), a change inside an existing page half-adopted (failed-payments page and the $500 scope, 3/5), and
a needed fact never asked for (a1-07 family, a0-05-T, a0-02-A). In each, the deciding record is builder-visible and
published (or, for the retry date, uniquely determined by published status cards).

**Reproducible.** Runtime, resources, credentials, network and rate limits are declared (`DESIGN_DOC.md` section
8). Fixed: LiteLLM's retry dependency (`tenacity`) was missing, so a retried call crashed the agent (1 of 450
conversations, none in runs 6-9); now pinned and tested. Provider failures are marked invalid, never scored. A full
host disk cut off run 9's grading (an infrastructure failure, not a result); it was regraded from its saved agents.

**Non-brittle.** Four verifier defects were found and fixed, each with a test:

| Defect | Effect | Fix |
|---|---|---|
| Concept phrases matched by one wording | correct explanations failed: "Redwood's canonical tokenization" where "Redwood's tokenizer" passed, "the complete request" where "the full request" passed (5 conversations across runs 6-8, 1 for the oracle) | the tokenizer and payload checks accept the same concept in other words; vague or echoed text still fails |
| a0-08 and a1-02 required stating the credit limit | the duplicate pages say to report the dispute, not the limit | the limit values removed |
| Q10 showed R1's 2027 invoices to 2026 conversations | an agent asked "what do we owe?" saw the future | each conversation's console holds its own release's records |
| a0-02-C's conflict was ambiguous | see Unambiguous | rewritten |

Rescored with every fix: run 5 Q00 0.828, Q10 0.862, Q11 0.900; run 6 0.862, 0.857, 0.867; run 7 0.767, 0.793,
0.833; run 8 0.833, 0.967, 0.967; run 9 (graded with every fix) 0.80, 0.733, 0.90. **Reward stays 0 for all five**; the oracle scores 1.0 on every key.

**Also checked:** across about 1,270 worded lines in runs 5-9 and the oracle, the simulated developer invents no
values, names no team, answers nothing asked in chat and gives no answer away (the few lines with an extra value
repeat what the agent had just said); every ending matches the console; all ten builder steps of runs 5-9 show no
hidden path, network fetch or early R1 data (run 9's own `workspace/tests/` folder is the builder's, not the hidden
`/tests`); run 9 ran on `anthropic/claude-opus-5.5` in both steps; a do-nothing agent fails all 60 cases. The oracle
passes every case run 9 failed in 5 of 5 runs.

**Open limitation:** the regression check replays 2026 conversations against 2027 knowledge (see Limitations);
the $500-scope evidence that depends on it is weighted less.

## Realism and economic value

Every deployed support agent has an owner, and most of the owner's work is not the first build: it is keeping the
agent right as the company changes underneath it. Prices are revised, a limit changes for new invoices but not old
ones, a procedure is withdrawn, a back-end changes its record format, and the documents that say so arrive as edits
to existing pages, mixed with drafts and chatter. This
task is that job: the builder evolves an agent it built six months earlier from a revised, noisy company export, and
is graded on whether the maintained agent absorbs the change (`drift`), keeps what still applies (`regression`) and
handles the new world (`q11`). A maintenance mistake here is a real cost that nobody notices until a customer does:
a credit refused that policy allows, a case opened that policy says not to, a customer told the wrong retry date.

Cost per trial: the builder averages $16.22 by Claude Code's meter, and grading 120 conversations on gpt-6-luna
averages $0.67 (measured from recorded tokens; 70-82% of input tokens are cache hits). Grading takes 60-90 minutes at
2-4 workers and needs a provider rate limit of 500K-1M tokens per minute.

## Limitations

- **One change cycle.** The task measures one evolution step (R0 to R1); repeated maintenance over several
  releases, where errors could compound, is not tested.
- **Few trials.** Five target-model trials; the evolution failures appear in three of five, the build failure in
  all five. With 30 cases per release one case moves a rate by 0.033, and the gate allows three misses and one case
  of regression slack. The support model is sampled, so a case can flip between runs.
- **Old conversations, old dates.** `regression` replays the R0 conversations on the maintained agent with their
  original 2026 dates, while its knowledge is from 2027. One agent reasoned that a page revised in 2027 was not yet
  valid on that date; the regression evidence that depends on this is weighted less.
- **The support model is gpt-6-luna**, for cost. Maintenance is judged through how that model uses the maintained
  agent; results with a Claude support model are not reported.
- **Weight.** A full trial takes about 4.5 h and needs a provider key for the support model.
- **Fixes after the runs.** The audit fixes above landed after these runs; their scores are reported as graded and
  as rescored.

## Reproduce

```sh
harbor run -p ./redwood-billing-support -a oracle -o "$PWD/jobs"
harbor run -p ./redwood-billing-support -a claude-code -m claude-opus-5-5 --ak reasoning_effort=high -o "$PWD/jobs"
harbor view ./jobs
python redwood-billing-support/scripts/curate_evidence.py jobs/<job>/<trial> <label>   # refresh evidence/
```

Harbor 0.22.0 names the Claude Code agent `claude-code`; the brief's `-a claude -m opus-5-5` maps to the second
command.
