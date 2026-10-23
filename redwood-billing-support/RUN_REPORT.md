# Run report: redwood-billing-support

## Summary

`claude-opus-5-5` at `reasoning_effort=high` (Claude Code) scores **reward 0 in 3/3 runs**; the oracle scores
**1.0 in 5/5**. Two evolution failures:

**1. Maintenance only adds, it never repairs (3/3 runs).** A rule Opus's first build fails to follow stays broken
after the update, in every run. When a developer reports more billed tokens than they counted and asks "can someone
check the meter?", the routing page says *"Answer, don't hand off: how billed tokens are counted (model-native
tokenizer, full request payload) … explain it and stop there, even if the customer calls it a bug."* The rule is the
same in R0 and R1. Opus's A0 escalates these cases to Billing Engineering (a0-01 and its variants, a0-09-T), and
after a full maintenance pass its A1 still fails them, in all three runs (3, 3 and 5 cases): the update rewrites
what changed and never re-tests what was already wrong.

**2. A change inside an existing page is only half-adopted (2/3 runs).** R1 revises the failed-payments page in
place: a payment that fails inside a processor incident now needs **no case**, and its retry date follows from past
status cards (3 business days after the window ends: April 12). Run 5's maintained agent still followed the
superseded procedure and opened a Billing Ops case; run 6's dropped the case but counted calendar days and told the
developer **April 10**; only run 7 got both right. The same pattern shows on Finance's approvals matrix, also
revised in place: in runs 5 and 6 the maintained agent lost the $500 limit that still applies to 2026 invoices and
handed off duplicates its R0 build had credited (that evidence comes from Q10, which replays 2026 conversations
against 2027 knowledge, so we weight it less).

The oracle passes the failed-payments and matrix cases in every run (25/25) and the tokenizer cases in 48 of 50.

The failure survives the fairness audit below: rescored with every case or verifier defect it found removed, all
three runs still score 0.

| Agent | Run | reward | q00 | q11 | drift | regression | behaviour_robustness | artifact_quality |
|---|---|---|---|---|---|---|---|---|
| Oracle | `evidence/oracle-final` (submitted version) | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24` | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24-b` | **1.0** | 1.0 | 0.967 | 0.96 | 0.967 | 0.978 | 1.0 |
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
| **No restraint** | a0-01 and its variants, a0-09-T | 5, 6, 7 | Explained part of the token gap, then escalated "check the meter" to Billing Engineering, or hedged that it could not tell | The routing page: answer, don't hand off, when the published pages explain it (model-native tokenizer, full request payload), "even if the customer calls it a bug". The Billing Engineering row needs request IDs that still mismatch; the developer has none. |
| **Replaced instead of scoped** | a0-08 (run 5), a0-07-C and a0-08-T (run 6) | 5, 6 | A1 could not apply the $500 limit to a 2026 invoice: escalated a creditable duplicate, or credited then escalated | R1's approvals matrix: $250 only for invoices on the 2027 price book, $500 still for earlier ones |
| **Rule never derived** | a1-03, a1-03-T | 5, 6 | Declined to give the payment-retry date | Four past status cards fix it: retries complete 3 business days after the incident window ends (run 7's build got it) |
| **Never asked** | a1-07 and its variants; a0-02-A, a1-04-A | 5, 6, 7 | Reported a total without asking when the runaway script ran, or picked one reading of an ambiguous ask | The developer gives the date (or the line) through the form when asked; that day's two requests total 17,683,825 tokens |
| **Wrong route** | a0-05-T, a1-04-A | 7 | Asked in chat whether a budget complaint was a relief request; sent an export mismatch to Billing Engineering | Routing: a request for relief on correct charges goes to Finance; an invoice-vs-export mismatch to Billing Ops |

**Classification** (the brief's terms):
- **Wrong hypothesis / not operationalised** (no restraint; rule never derived): retrieval worked (the agents quote
  the rule's own words), but the build shipped "escalate when unsure" instead of the rule.
- **Brittle patch** (replaced instead of scoped): the R1 update overwrote the $500 limit instead of scoping it by
  date, breaking behaviour the R0 build got right. `regression` measures it.
- **Incomplete inspection** (never asked; wrong route): the agent stopped at the first plausible reading.
- **Skipped verification:** practice was mostly the provided example scripts plus a few of the builder's own (run
  5: an SLA claim and a card-processor failure). None touched a tokenizer gap, a retry date or a date-scoped limit.

**Recurring.** The restraint failure appears in every Opus run (and in runs 1-4 on earlier case sets); "never
asked" in every run; the scoped-limit regression in two of three; the retry-date inference in two of three (and in
every earlier build).

## Oracle

`steps/*/solution/solve.sh` installs a reference workspace (hybrid retrieval over embedded billing records, a
billing-investigation skill and `billing_rules.py` that state each rule, a policy subagent, hooks), builds its
index with the same local embedder, and on R1 embeds only the new and revised records. It runs through the same
conversation loop, sandbox and console as the builder's agent, with no shortcut. Five runs, reward 1.0 each (the last on exactly the submitted version); it
misses at most two cases per run, a different one each time (the support model is sampled).

## Fairness audit

Every failing conversation in runs 5, 6 and 7 was read, against the records that state its rule. Findings:

**Fair (agent capability):** every pattern in the table above. In each, the deciding record is builder-visible,
the oracle passes the case, and the agent either did not act on a rule it retrieved or did not ask for a fact the
developer would have given.

**Fixed (not the agent's fault), and rescored:**

| Finding | Why it was unfair | Fix |
|---|---|---|
| a0-02-C was ambiguous | its "conflict" was a $980 figure from the customer's own dashboard, which fits the routing row for invoice/export mismatches; an oracle run and Opus run 6 both defensibly routed it to Billing Ops | rewritten as a false policy claim the retries page refutes; the oracle passes it 3/3 |
| a0-08 and a1-02 required saying the limit ($500, $250) | the duplicate pages tell Support to say what it did (a dispute is open), not to state the limit; the dispute itself is already checked on the console | the two values removed (hidden requirement) |
| Q10 showed 2027 invoices to 2026 conversations | Q10 replays R0 cases on the R1 console, which also held R1's later invoices; an agent asked "what do we owe?" saw the future (a0-10-A in runs 6 and 7, a0-10-M for the oracle) | each conversation's console now holds its own release's records in the console release's format (test-enforced) |
| A retried model call could crash the support agent | LiteLLM needs `tenacity` to retry (e.g. after a 429) and it was not installed; 1 of 450 graded conversations crashed (an oracle Q01 case), none in runs 6 or 7 | `tenacity` pinned, with a test |

Rescored with these defects removed (a defect case excluded, as invalid; a removed value no longer required):
run 5 Q00 0.828, Q10 0.862, Q11 0.90; run 6 0.862, 0.821, 0.867; run 7 0.767, 0.759, 0.833. **Reward stays 0 for
all three.**

**Checked and clean:**
- **No near-miss phrasing.** Every failure on `communicate` alone was read: in each, the agent said a different
  value (a stale date, a stale threshold, the wrong error code) or hedged, not the right value in other words.
- **Simulated developer.** Audited over runs 5, 6 and 7 and the oracle (about 660 worded lines): no team or rule
  names, no answers to questions asked in chat, no required value said before the agent said it, and every ending
  matches the console. Three lines repeated values the agent had just given ("thanks for confirming the $340.00
  credit note"); the wording model sees only the transcript, never the script's facts, so these are echoes.
- **No leakage.** All six builder steps of runs 5-7 were audited: no hidden path, no network fetch, and no R1 record
  reached a builder before R1.
  `/tests`, seeds, overlays and the practice service are root-only; graded agents run as `nobody` after `/tests`
  is made root-only. A grading-time leak (the task folder mounted world-readable) was found and closed earlier; no
  workspace had used it. Identifier collisions that could pass a check by accident were removed (`req_pb402`).
- **Shallow solutions fail.** A do-nothing agent fails all 60 cases, and the unmaintained A0 passes 4 to 8 of 30 R1
  cases (Q01), both test- or run-verified.
- **No LLM judge.** A model only rewords scripted lines; every verdict is reproducible from the case log.

## Realism and economic value

Support agents grounded in company knowledge are among the most deployed LLM applications, and their cost is not
the first build but keeping them right as pricing, policy and data formats change. This task is that job: a noisy
multi-platform export, rules owned by Finance, Security and Support, drafts and superseded pages, a breaking
console change, and a six-month gap. A wrong answer here is a real cost (an unneeded engineering ticket, a credit
over the approval limit, a customer told the wrong retry date).

Cost per trial: the builder averages $16.22 (runs 5-7, by Claude Code's meter), and grading 120 conversations on
gpt-6-luna averages $0.67 (measured from recorded tokens; 70-82% of input tokens are cache hits). Grading takes 60-90 minutes at 2-4
workers and needs a provider rate limit of 500K-1M tokens per minute.

## Limitations

- Three target-model trials. 30 cases per release, so one case moves a rate by 0.033; the gate allows three misses
  and one case of regression slack. The support model is sampled, so a case can flip between runs.
- The support model is gpt-6-luna for cost; results with a Claude support model are not reported.
- A full trial takes about 4.5 h and needs a provider key for the support model.
- The audit fixes above landed after runs 5-7; their scores are reported as graded and as rescored.

## Reproduce

```sh
harbor run -p ./redwood-billing-support -a oracle -o "$PWD/jobs"
harbor run -p ./redwood-billing-support -a claude-code -m claude-opus-5-5 --ak reasoning_effort=high -o "$PWD/jobs"
harbor view ./jobs
python redwood-billing-support/scripts/curate_evidence.py jobs/<job>/<trial> <label>   # refresh evidence/
```

Harbor 0.22.0 names the Claude Code agent `claude-code`; the brief's `-a claude -m opus-5-5` maps to the second
command.
