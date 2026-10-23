# Run report: redwood-billing-support

## Summary

`claude-opus-5-5` at `reasoning_effort=high` (Claude Code) scores **reward 0 in 5/5 runs** (four on a Claude
subscription, one through OpenRouter); the oracle scores **1.0 in 6/6**.

**In every run, the build writes a rule down and never checks that its agent follows it.** When a developer reports
more billed tokens than they counted and asks "can someone check the meter?", the routing page says *"Answer, don't
hand off: how billed tokens are counted (model-native tokenizer, full request payload) … explain it and stop there,
even if the customer calls it a bug."* All five Opus builds copied this rule into their own skill files. In 19 of
the 23 failing conversations the agent retrieved the page during the conversation, and it still opened a case in
all 23. No build ever practised a token gap. This one failure keeps every build below the 0.9 bar (Q00 0.77-0.86
after every correction).

**When the world changes, a change inside an existing page is only half-adopted (3 of 5 runs).** R1 revises the
failed-payments page in place: a payment that fails inside a processor incident now needs **no case**, and its retry
date follows from past status cards (April 12). Run 5's maintained agent still followed the superseded procedure and
opened a case; runs 6 and 9 dropped the case but told the developer **April 10** (run 9 had even written a practice
script for this exact situation, and checked only that a date came back). In the same three runs the maintained
agent lost the $500 limit that the revised approvals matrix keeps for older invoices. Runs 7 and 8 maintained these
correctly, and run 8's maintenance even repaired most of the build's tokenizer escalations (3 of 5 pass in Q10). The oracle passes all of these
cases in every run.

The failure survives the fairness audit below: rescored with every case or verifier defect it found removed, all
five runs still score 0.

| Agent | Run | reward | q00 | q11 | drift | regression | behaviour_robustness | artifact_quality |
|---|---|---|---|---|---|---|---|---|
| Oracle | `evidence/oracle-submitted` (exactly the submitted code) | **1.0** | 1.0 | 0.933 | 0.923 | 1.0 | 0.956 | 1.0 |
| Oracle | `evidence/oracle-final` | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24` | **1.0** | 1.0 | 1.0 | 1.0 | 0.967 | 0.978 | 1.0 |
| Oracle | `evidence/oracle-2026-10-24-b` | **1.0** | 1.0 | 0.967 | 0.96 | 0.967 | 0.978 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run9-openrouter` (via OpenRouter) | **0.0** | 0.80 | 0.90 | 0.88 | 0.833 | 0.846 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run8` | **0.0** | 0.833 | 0.967 | 0.96 | 1.0 | 0.974 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run6` | **0.0** | 0.833 | 0.867 | 0.818 | 0.84 | 0.756 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run7` | **0.0** | 0.733 | 0.80 | 0.75 | 0.955 | 0.889 | 1.0 |
| Opus 5.5 | `evidence/opus-5-5-run5` (earlier version) | **0.0** | 0.833 | 0.90 | 0.80 | 0.96 | 1.0 | 1.0 |

Two more oracle runs (`evidence/oracle`, an earlier version; and one graded in Harbor on 2026-10-23) also scored
1.0: six of six. Every case Opus fails, the oracle passes in at least 4 of the 5 curated oracle runs.

**The failure in one line:** Opus writes each rule as a condition for the support model to judge at run time
("if a current page explains what the developer sees, answer; no case"). It then checks only that its agent runs,
never that the agent makes the right decision on the case the rule is for. The oracle states each decision for its
case type (its billing-investigation skill and `billing_rules.py`) and passes. Where an Opus build turned a rule
into code (runs 7 and 8 compute the retry date), it passed that case too.

**Why the failure is the builder's and not the support model's.** Both use the same support model (gpt-6-luna):
the oracle's build passes with it in every run. Writing the missing a1-03 rule into the prompt
(`REDWOOD_REPLAY_HINT`, a diagnostic replay) makes the support agent answer a1-03 correctly. Whether a case passes
follows what the build delivered: the two A1 builds that contain retry-date logic pass a1-03, and the three without
it fail it.

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
- **Versions.** Runs 6-9 and the oracle runs dated 2026-10-24 use the submitted design: grading inside
  `maintain_r1`'s verifier, LiteLLM, Q01 on all 30 R1 cases. Run 5 and `evidence/oracle` are the previous version
  (graded after the job by `scripts/grade_snapshots.sh`, OpenAI Responses API, Q01 on base cases only); same case
  set, checks and gate. `oracle-submitted` also includes the last packaging change: the LiteLLM gateway moved into
  `redwood_kit`, so no `tests/` file is copied into the image. Scoring is unchanged.

## Genuine failure modes

A pattern is listed as a failure mode only if all of these hold:
- it recurs in at least two of the five Opus runs;
- the oracle passes the same cases;
- the deciding record is visible to the builder;
- the failing check is one that survived the verifier fixes in the fairness audit;
- the delivered agent's files or the conversation show the mechanism.

The `failed/` folders of runs 5-8 hold results as originally graded. A few of those conversations failed only
on checks the audit later removed: "never said 500" on a0-08, and tokenizer explanations worded differently. They
are excluded below.

Fail counts by case family (conversations failed out of conversations run; oracle = the five curated oracle runs):

| Case family | Q | Opus run 5 / 6 / 7 / 8 / 9 | Opus total | Oracle |
|---|---|---|---|---|
| Token gap: a0-01 and its 3 variants, a0-09-T | Q00 | 5 / 4 / 5 / 4 / 5 | 23 of 25 | 0 of 25 |
| Retry date: a1-03, a1-03-T | Q11 | 2 / 2 / 0 / 0 / 2 | 6 of 10 | 0 of 10 |
| $500 scope on older invoices: a0-07, a0-08 families (a db failure only) | Q10 | 1 / 2 / 0 / 0 / 3 | 6 | 0 |
| Runaway-script tokens: a1-07 and its 3 variants | Q11 | 1 / 2 / 4 / 1 / 1 | 9 of 20 | 0 of 20 |

### 1. The rule is written down but never tested on its case (build; 5 of 5 runs)

**What happens.** A developer reports about 6% more billed tokens than their local count and asks for a meter
check. The agent explains part of the gap, then hands the case to Billing Engineering. 22 of the 23 failures went
to Billing Engineering; one went to Billing Ops.

**The rule was in every build's own files:**
- run 8's skill: *"Explain-only: how tokens/retries/streams are billed … Answer from the pages and stop; no case,
  even if the developer calls it a bug"*;
- run 9's: *"Explained by a policy page: explain with their own numbers; no case, even if they call it a bug"*;
- runs 5-7 say the same.

**The rule was in front of the agent.** In 19 of the 23 failing conversations, the routing passage ("model-native
tokenizer, full request payload") came back from `search_knowledge` in that same conversation.

**Why it still fails.** Every build phrased the rule as a condition the support model has to evaluate: "if a
current page explains what the developer sees". The question is about embeddings and a 6% gap, so the agent decided
the general page did not explain *this* gap. The developer's "can someone check the meter?" then settled it. In
run 8 the build's own policy-researcher subagent reported that "current published policy does not specify
embeddings' tokenizer", and the agent escalated on that answer. The oracle's skill names the case type instead
(token-count gap: explain the counting basis, then stop) and passes 25 of 25.

**Verification skipped.** None of the five builder sessions ran a practice conversation about a token gap. The
five provided practice scripts cover duplicates, a marketplace request and an SLA claim; the builders wrote their
own for other topics.

**In the brief's terms:** skipped verification, and a rule that was retrieved but not operationalised.

### 2. A revised page is half-adopted during maintenance (evolution; runs 5, 6 and 9)

R1 revises the failed-payments page in place. A payment that fails during a processor incident now needs no
case, and the retry date follows from past status cards: retries complete 3 business days after the incident
window ends, which gives April 12.

**What the maintained agents did:**
- Run 5's A1 still followed the superseded procedure and opened a case.
- Runs 6 and 9 dropped the case but told the developer April 10, a Saturday reached by counting calendar days.
- Run 9 had written a practice script for exactly this situation, and checked only that a date came back.

**The split follows the delivered agent.** The A1 of runs 7 and 8 contains business-day retry logic (run 8 in its
skill and `tools.py`, run 7 in `tools.py`), and both pass a1-03 and a1-03-T. The A1 of runs 5, 6 and 9 has none,
and all three fail both cases.

**The same three runs lost a scope.** R1's approvals matrix sets a $250 limit for invoices on the 2027 price book
and keeps $500 for earlier ones. Applied to a 2026 invoice:
- run 5's A1 did not open the dispute for a creditable duplicate (a0-08);
- runs 6 and 9 refused or escalated credits the $500 limit allows (a0-07-A, a0-07-C, a0-08-T).

Run 8's A1 parses the per-price-book limit in code. Run 7 left it to retrieval and still passed, so for the limit
the split is not simply code against prose. This evidence depends on replaying 2026 conversations against a 2027
agent (see Limitations) and is weighted less.

**In the brief's terms:** a brittle patch, and bad state tracking across a revision.

### 3. The missing fact is noticed but not asked for (support behaviour the build leaves open; 5 of 5 runs)

**What happens.** The developer asks how much their March invoice was, how many tokens a runaway script used, and
for a goodwill credit. In 8 of the 9 failures the agent states the gap itself: "the console doesn't identify which
requests came from the runaway script, so I can't confirm the script's token count". It never asks when the script
ran. The developer would answer March 17 through `ask_developer`, and that day's requests total 17,683,825 tokens.
The agents report the invoice's 69,941,603 tokens, or decline to give a figure.

**The oracle contrast.** The oracle's skill tells the agent to ask for the time of a charge or count that looks
too high, and passes 20 of 20. This mode depends partly on the support model, but the oracle shows a build can
close it.

**In the brief's terms:** incomplete inspection.

### Recurring but minor, and one-off misses (not claimed as modes)

**Recurring in 2 of 5 runs:**
- a0-05-T: the agent asks in chat whether a budget complaint is a relief request, instead of using the form (runs 7
  and 8);
- a0-10-A (runs 6 and 7);
- a0-02-A (runs 7 and 9).

**One-off:**
- a0-04-T and a1-04-A: wrong team;
- a1-02;
- run 9's untested guardrail. Its own credit hook required citing a passage id. The agent cited one it had not
  retrieved, the hook blocked a valid $340 credit, and the agent told the developer the console had rejected it
  (a0-08-T).

The oracle's own misses (a0-01-A and a0-01-M in Q10, a0-10-M, a1-02-T, a1-04-C and a1-10-A, once each) are a different
case each time. Each is a sampling miss by the support model, within the gate's slack.

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
revised records. Six runs, reward 1.0 each (`oracle-submitted` on exactly the submitted code); it misses at most a case or
two per run to the support model's sampling, a different one each time.

## Fairness audit

Every failing conversation of Opus runs 5-9 was read against the records that state its rule. By the brief's
criteria:

**Solvable.** The oracle scores reward 1.0 in 6/6 runs, `oracle-submitted` on exactly the submitted code, through the
same conversation loop, console and support model. Every case Opus fails, the oracle passes in at least 4 of 5
runs. Every rule cites builder-visible records per release (test-enforced).

**Unambiguous.** The instructions state the deliverable, interfaces, limits and success measure, and leak no rule
value, team, threshold or answer (checked by scan). One case was ambiguous and was rewritten: a0-02-C's "conflict"
fit a routing row, and two agents defensibly routed it (now a false policy claim the retries page refutes; the oracle
passes it 3/3). Cases that are ambiguous by design (missing-info and ambiguous variants) are resolved by the
developer's form, which the contract tells the agent to use: a0-05-T fails only when the agent asks in chat instead.

**Substantive.** The remaining failures are capability gaps: a rule written into the build and retrieved, but never tested on its case (tokenizer
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
0.833; run 8 0.833, 0.967, 0.967; run 9 (graded with every fix) 0.80, 0.733, 0.90. **Reward stays 0 for all five**; the oracle keeps reward 1.0.

**Also checked:** across about 1,270 worded lines in runs 5-9 and the oracle, the simulated developer invents no
values, names no team, answers nothing asked in chat and gives no answer away (the few lines with an extra value
repeat what the agent had just said); every ending matches the console; all ten builder steps of runs 5-9 show no
hidden path, network fetch or early R1 data (run 9's own `workspace/tests/` folder is the builder's, not the hidden
`/tests`); run 9 ran on `anthropic/claude-opus-5.5` in both steps; a do-nothing agent fails all 60 cases. The oracle
passes every case run 9 failed in at least 4 of 5 curated runs.

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
set -a; . ./.env; set +a                                  # provider keys for the builder and the support model
harbor run -p ./redwood-billing-support -a oracle -o "$PWD/jobs"
harbor run -p ./redwood-billing-support -a claude-code -m claude-opus-5-5 --resume-trajectory \
    --ak reasoning_effort=high --ak max_budget_usd=30 -o "$PWD/jobs"
harbor view ./jobs
python redwood-billing-support/scripts/curate_evidence.py jobs/<job>/<trial> <label>   # refresh evidence/
```

Harbor 0.22.0 names the Claude Code agent `claude-code`; the brief's `-a claude -m opus-5-5` maps to the second
command. `--resume-trajectory` is required: step 2 must continue the builder's step-1 session, and a run that starts
afresh is marked invalid.
