# Run report: redwood-billing-support

## Summary

`claude-opus-5-5` at `reasoning_effort=high` (Claude Code) scores **reward 0 in 4/4 runs**; the oracle scores
**1.0 in 5/5**.

**In every run, the build breaks a published rule it retrieved.** When a developer reports more billed tokens than
they counted and asks "can someone check the meter?", the routing page says *"Answer, don't hand off: how billed
tokens are counted (model-native tokenizer, full request payload) … explain it and stop there, even if the customer
calls it a bug."* Opus's R0 agent escalates these cases to Billing Engineering in all four runs (a0-01 and its
variants, a0-09-T), which alone keeps the build below the 0.9 bar (Q00 0.77-0.86 after every correction).

**When the world changes, a change inside an existing page is only half-adopted (2 of 4 runs).** R1 revises the
failed-payments page in place: a payment that fails inside a processor incident now needs **no case**, and its retry
date follows from past status cards (April 12). Run 5's maintained agent still followed the superseded procedure and
opened a case; run 6's dropped the case but told the developer **April 10**. In the same two runs the maintained
agent lost the $500 limit that the revised approvals matrix keeps for older invoices. Runs 7 and 8 maintained these
correctly, and run 8's maintenance even repaired the build's tokenizer escalations. The oracle passes all of these
cases in every run.

The failure survives the fairness audit below: rescored with every case or verifier defect it found removed, all
four runs still score 0.

| Agent | Run | reward | q00 | q11 | drift | regression | behaviour_robustness | artifact_quality |
|---|---|---|---|---|---|---|---|---|
| Oracle | `evidence/oracle-final` (submitted version) | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24` | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24-b` | **1.0** | 1.0 | 0.967 | 0.96 | 0.967 | 0.978 | 1.0 |
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
  marks the run invalid if it does not.
- **Deployed support model and developer wording:** `gpt-6-luna` on Azure AI Foundry (reasoning high for the
  support agent, low for wording), chosen for cost. It is a parameter (`SUPPORT_MODEL`, `USER_MODEL`, LiteLLM ids)
  that the builder-visible manifest and instructions name. Practice and grading use the same model.
- **Versions.** Runs 6, 7 and the two `oracle-2026-10-24*` runs use the submitted version: grading inside
  `maintain_r1`'s verifier, LiteLLM, Q01 on all 30 R1 cases. Run 5 and `evidence/oracle` are the previous version
  (graded after the job by `scripts/grade_snapshots.sh`, OpenAI Responses API, Q01 on base cases only); same case
  set, checks and gate.

## The failure, in detail

Failures across the three Opus runs, grouped by what went wrong (each conversation is linked from its run's
`evidence/<run>/README.md`):

| Pattern | Cases | Runs | What the agent did | What the records say |
|---|---|---|---|---|
| **No restraint** | a0-01 and its variants, a0-09-T | 5, 6, 7, 8 | Explained part of the token gap, then escalated "check the meter" to Billing Engineering, or hedged that it could not tell | The routing page: answer, don't hand off, when the published pages explain it (model-native tokenizer, full request payload), "even if the customer calls it a bug". The Billing Engineering row needs request IDs that still mismatch; the developer has none. |
| **Replaced instead of scoped** | a0-08 (run 5), a0-07-C and a0-08-T (run 6) | 5, 6 | A1 could not apply the $500 limit to a 2026 invoice: escalated a creditable duplicate, or credited then escalated | R1's approvals matrix: $250 only for invoices on the 2027 price book, $500 still for earlier ones |
| **Rule never derived** | a1-03, a1-03-T | 5, 6 | Declined to give the payment-retry date | Four past status cards fix it: retries complete 3 business days after the incident window ends (run 7's build got it) |
| **Never asked** | a1-07 and its variants; a0-02-A, a1-04-A | 5, 6, 7, 8 | Reported a total without asking when the runaway script ran, or picked one reading of an ambiguous ask | The developer gives the date (or the line) through the form when asked; that day's two requests total 17,683,825 tokens |
| **Wrong route** | a0-05-T, a1-04-A | 7, 8 | Asked in chat whether a budget complaint was a relief request; sent an export mismatch to Billing Engineering | Routing: a request for relief on correct charges goes to Finance; an invoice-vs-export mismatch to Billing Ops |

**Classification** (the brief's terms):
- **Wrong hypothesis / not operationalised** (no restraint; rule never derived): retrieval worked (the agents quote
  the rule's own words), but the build shipped "escalate when unsure" instead of the rule.
- **Brittle patch** (replaced instead of scoped): the R1 update overwrote the $500 limit instead of scoping it by
  date, breaking behaviour the R0 build got right. `regression` measures it.
- **Incomplete inspection** (never asked; wrong route): the agent stopped at the first plausible reading.
- **Skipped verification:** practice was mostly the provided example scripts plus a few of the builder's own (run
  5: an SLA claim and a card-processor failure). None touched a tokenizer gap, a retry date or a date-scoped limit.

**Recurring.** The restraint failure appears in every Opus run (and in runs 1-4 on earlier case sets); "never
asked" in every run; the scoped-limit regression in two of four; the retry-date inference in two of four (and in
every earlier build).

## Oracle

`steps/*/solution/solve.sh` installs a reference workspace (hybrid retrieval over embedded billing records, a
billing-investigation skill and `billing_rules.py` that state each rule, a policy subagent, hooks), builds its
index with the same local embedder, and on R1 embeds only the new and revised records. It runs through the same
conversation loop, sandbox and console as the builder's agent, with no shortcut. Five runs, reward 1.0 each (the last on exactly the submitted version); it
misses at most two cases per run, a different one each time (the support model is sampled).

## Fairness audit

Every failing conversation of Opus runs 5-8 was read against the records that state its rule. By the brief's
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
escalations, 4/4), a change inside an existing page half-adopted (failed-payments page and the $500 scope, 2/4), and
a needed fact never asked for (a1-07 family, a0-05-T, a0-02-A). In each, the deciding record is builder-visible and
published (or, for the retry date, uniquely determined by published status cards).

**Reproducible.** Runtime, resources, credentials, network and rate limits are declared (`DESIGN_DOC.md` section
6). Fixed: LiteLLM's retry dependency (`tenacity`) was missing, so a retried call crashed the agent (1 of 450
conversations, none in runs 6-8); now pinned and tested. Provider failures are marked invalid, never scored.

**Non-brittle.** Four verifier defects were found and fixed, each with a test:

| Defect | Effect | Fix |
|---|---|---|
| Concept phrases matched by one wording | correct explanations failed: "Redwood's canonical tokenization" where "Redwood's tokenizer" passed, "the complete request" where "the full request" passed (5 conversations across runs 6-8, 1 for the oracle) | the tokenizer and payload checks accept the same concept in other words; vague or echoed text still fails |
| a0-08 and a1-02 required stating the credit limit | the duplicate pages say to report the dispute, not the limit | the limit values removed |
| Q10 showed R1's 2027 invoices to 2026 conversations | an agent asked "what do we owe?" saw the future | each conversation's console holds its own release's records |
| a0-02-C's conflict was ambiguous | see Unambiguous | rewritten |

Rescored with every fix: run 5 Q00 0.828, Q10 0.862, Q11 0.900; run 6 0.862, 0.857, 0.867; run 7 0.767, 0.793,
0.833; run 8 0.833, 0.967, 0.967. **Reward stays 0 for all four**; the oracle scores 1.0 on every key.

**Also checked:** the simulated developer invents no values, names no team, answers nothing asked in chat and gives
no answer away (three lines echoed the agent's own words); every ending matches the console; all eight builder steps
of runs 5-8 show no hidden path, network fetch or early R1 data; a do-nothing agent fails all 60 cases.

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
- **Few trials.** Four target-model trials; the evolution failures appear in two of four, the build failure in
  all four. With 30 cases per release one case moves a rate by 0.033, and the gate allows three misses and one case
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
