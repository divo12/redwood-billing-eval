# Design Doc: redwood-billing-support

`Task.md` is the exhaustive spec (every rule, case, check and decision). This document is the reviewer's version:
what the task is, why it is built this way, where it is hard, and why it is fair.

## 1. What this task is

We have a company called Redwood Inference ,a fictional LLM-inference company which has a lot of messy , raw export of company records across multiple channels(Slack, Gmail, Jira, Confluence, Drive, HubSpot and more). We have a builder agent Claude Opus 5.5 in Claude Code who is tasked with 2 things: 1st build an initial version V0 of a support agent tasked at resolving billing issues for developers using redwood's platform and then as time moves on, the company policies, docs, customers evolve, the builder also has to evolve the billing support agent.
Most records have nothing to do with billing, and the billing ones mix current policy with drafts, superseded pages and chatter. Nothing says which records matter. The builder must find the rules and ship an agent and make its different harness components(prompt, rules, skills, tools, hooks, RAG , other components). Then, in the same session, the export grows by 3,547 records: new rules land on top of old ones, three pages are revised in place, some changes apply only from a date onward, and the console's record format changes. The builder
must judge which newer record supersedes which older one and evolve its agent without breaking what still applies.
The deliverable is the agent itself: both versions hold 120 graded conversations with a scripted developer, and grading reads the billing console's end state and what the  agent said, by code, never an LLM judge.

## 2. Why this design (methodology lineage)

Built this task after reviewing hyper-tau bench, -- hyper tau's benchmark is not at all copied in it, although some of the verifier checks are inspired but the task , environment and checks are made by me.

On reviewing Hyper Tau bench, there were 2-3 fundamental limitation, as more and more humans trust agents , we need to have agents that not only build an agent but also have the ability to evolve them given the change in distribution of data.

Secondly , Hyper Tau Bench doesn't have an exhaustive environment like it doesn't replicates a real enterprise but a real enterprise has messy docs across multiple channels like slack , Gmail etc.

Thirdly, Hyper tau bench only targets prompt, tools, policies whereas an agent has different components and generally support agents have a lot of retrieval/ranking algos which was never touched upon by them

We present a novel task that not only measures how an agent builds another agent through verifier checks like DB end state, Tool actions, NL assertions but also measures the agent's ability to evolve/maintain a agent given changing information

## 3. The core capability under test

Not "can the model build a RAG agent" (that's a solved capability by itself). The target capability is: when new information lands on top of old, can the agent maintain the agent it built -- decide which new records should change it, change exactly those rules, and keep the old ones that still apply? New is not always current (drafts, chatter), and current is not always total (a $250 limit that applies only to 2027 invoices). An agent that rebuilds from the latest text, or bolts new facts on without scoping them, will look maintained right up until a customer asks about last year's invoice.

## 4. The designed traps

All of these are real and present in the shipped content (`Task.md` names each record); none is a hidden
requirement. They come in four groups: finding the rules, keeping them right when the world changes, applying
them in a live conversation, and testing all of that before it ships.

**Finding the rules (step 1)**

1. **Noise at enterprise scale.** 3,524 records across nine platforms; most are SRE runbooks, sales threads,
   partner deals and products that have nothing to do with billing. Embedding runs on 2 CPUs and the instruction
   forbids embedding everything, so the builder must decide what is billing knowledge before it can index it.
2. **Authority, not recency.** Every scored rule has a plausible competitor: a draft price-book plan in Drive,
   agents' "everything is urgent" chatter in Slack, a provisional Finance x Support meeting, a superseded 2024
   thread letting Support grant goodwill under $200, an old RACI draft that routes meter questions to Telemetry,
   and 14 look-alike pages in the same spaces. Only status, owner and date tell them apart.
3. **Scattered rules.** No record states a whole rule; each piece names the next by key or title. The credits page
   names Finance's approvals matrix, which holds the limit; the escalation priority is a #support announcement the
   format page names; which table balances come from is a decision recorded in a Fireflies meeting. A builder that
   indexes one page per topic gets half a rule.
4. **Inference, not lookup.** The payment-retry date after a processor incident is stated nowhere. Four past status
   cards fix it uniquely (retries complete 3 business days after the incident window ends), and the agent must
   work it out for a new incident. *Opus failed a1-03 in every run, on A0 and A1.*
5. **Restraint is a rule too.** The routing page says to *answer*, not hand off, when the published pages explain
   what the customer sees (tokenizer counts, billed retries, console lag), "even if the customer calls it a bug".
   Escalating feels safe and is wrong. *The a0-01 tokenizer cases and a0-09-T failed in both Opus runs (run 6 passed
   only the plain a0-01, not its three variants): the agent retrieved the rule, then escalated to Billing
   Engineering anyway.*
6. **Rules can't live in the prompt.** `system_prompt.md` and `AGENTS.md` are capped (1,500 and 4,000 characters)
   and may not hold exact billing rules, and no store of full source records is allowed. A rule has to become a
   skill step, a tool behaviour or a retrievable chunk the agent reliably uses: retrieving it is not enough.

**Keeping them right when the world changes (step 2)**

R1 is not a clean changelog. It mixes six kinds of change, and each kind has its own way to go wrong.

7. **Changed in place.** R1 revises the routing page, the failed-payments page and Finance's approvals matrix at
   their R0 paths with a later date. A builder that diffs file names, or keeps its R0 index, never sees the change.
8. **Superseded without saying so.** The R0 pages on failed card payments and card updates, credit balances and
   free-tier limits are replaced by newer ones, but the new pages carry effective dates, not "this replaces X". The old page
   stays in the export and stays retrievable; only comparing dates and status tells the agent which one governs.
   The developer often cites the old one ("your 2025 help page says Support refunds duplicates in full"), so the
   agent must say which page is current rather than follow the customer.
9. **Date-scoped changes.** The duplicate-credit limit drops to $250 only for invoices on the 2027 price book (only
   Finance's close calendar says that starts 1 March 2027); the usage-delay threshold becomes 60 minutes only after a
   cut-over that only a Linear ticket dates. The old rule still governs older invoices, and Q10 replays the R0
   conversations to check it. *Opus's A1 dropped the $500 that still applies to 2026 invoices (a0-08 in run 5;
   a0-07-C, a0-08, a0-08-T in run 6).*
10. **Contradictions planted next to every change.** Each new rule arrives with a newer-looking competitor: a Slack
    thread saying the delay threshold is 10 minutes, agents' "everything is urgent" chatter against the announced
    escalation priority (`normal`), a draft 2027 price-book plan in Drive, and an earlier provisional Finance x
    Support meeting that disagrees with the recorded decision. 11 more look-alike pages land in the same spaces.
    New is not the same as current.
11. **Near-miss facts.** The Free-tier quota error is named on the pricing page, but only the 2027 API error
    reference gives its code (`402 quota_exceeded`); `429`, the per-minute rate limit, is right there beside it.
    The billing month is now counted in the account's `billing_timezone`, but the format page only defines the field;
    the rule is in Finance's close calendar. An agent that stops at the first plausible answer gives the wrong one.
12. **New data, new source of truth.** R1 adds a `credit_ledger` table, and a Finance x Support decision says
    balances now come from it, not from credit grants. The old lookup still returns a number, just the wrong one.
13. **A breaking console change with no policy change ("stale" cases).** R1 serves money as `{value, currency}`,
    renames the account field on writable tables, adds `billing_timezone` and requires an escalation priority.
    Cases whose rules never changed (an invoice mismatch, a goodwill request) now fail on A0's tools, so a builder
    that only reads the new policy text and never re-tests its tools breaks working behaviour.
14. **A procedure withdrawn.** The card-update procedure is retracted in place; card digits must never be repeated
    (`never_say`), and the developer must be sent to their billing settings instead. Removing behaviour is
    maintenance too.

Measured together: Q01 grades the unmaintained A0 on the R1 cases (it passes 4 to 8 of 30), so `drift` shows how much
of the change A1 absorbed and `regression` shows what it broke. *Opus absorbs most of it (drift 0.80 and 0.82) and
misses the parts that need judgment: the date scope, and the retry date that no single record states.*

**Applying them in a conversation (graded)**

15. **Twins.** Five cases per release have a near-identical twin whose correct result is the opposite: only 20
    minutes have passed, a self-serve account has no SLA, the duplicate is exactly at the $250 limit, the developer
    counted only user messages. Matching the ask instead of checking the facts fails one half.
16. **Developers who make it harder.** Each non-twin case has three variants: the deciding facts are missing, the
    ask is ambiguous, or the developer asserts a false policy. Facts come only through the `ask_developer` form,
    never in chat, so the agent must ask the right question. *Opus never asked when a runaway script ran (a1-07),
    so it could not separate its tokens.*
17. **Consent and forbidden values.** Credits are graded on order: no credit before the developer's yes. The stale
    answer is itself checked (`never_say`): the calendar-day retry date, the UTC-month token total.
18. **The conversation's own clock.** Each conversation runs on its own date and time (libfaketime), so "has it been
    30 minutes?" and "which month is this?" depend on reading the clock, not the host's.

**Verification has to be designed, not borrowed**

19. **Five practice conversations per release.** The builder can test its agent on the real deployed model and a
    practice console, but only five times, and the example scripts cover only the practice accounts. Testing the
    hard rules means writing scripts for them. *Run 5 practised mostly on the provided examples; none touched a
    tokenizer gap, a retry date or a scoped limit.*
20. **One session, two deadlines.** Two hours to build, then one hour, in the same Claude Code session, to
    understand a 3,547-record change to its own system. A0 is frozen and graded as delivered.

For more failure ideas, see [`research.md`](research.md): failure modes for every part of a support agent's
harness (system prompt, AGENTS.md, guardrails and hooks, the agent loop, tools, skills, subagents, retrieval,
memory, handoff, budgets). Time allowed only part of it to be built into this task; its last section maps what
is implemented to the traps above.

## 5. Fairness audit against the assignment rubric

- **Solvable.** The oracle (`steps/*/solution/solve.sh`) builds a workspace through the same runtime, sandbox and
  console the builder's agent uses, and scores reward 1.0 in every run (Q00 and Q11 at 29 or 30 of 30). Every rule a
  case tests cites builder-visible records for its release (test-enforced). Every value the agent must say
  appears in a tool result, except the retry date, which the status cards determine uniquely.
- **Unambiguous.** The instructions state the deliverable, the interfaces, the limits and how success is
  measured. The agent contract documents every tool and field the developer form accepts. Gold actions and
  accepted alternatives cover every outcome the records allow (e.g. either duplicated line).
- **Substantive, long-horizon.** Corpus forensics, then architecture and indexing, then tools, skills, subagent
  and hooks, then practice and debugging; then diffing a changed corpus, re-indexing, scoped edits and regression
  checks. Two steps, about 3 hours, one session.
- **Reproducible.** Pinned base image digest, a full `requirements.lock`, an embedding model baked into the
  image, and fixed data. Model access is declared: the builder's harness, and one provider key for the deployed
  support model (Section 7).
- **Non-brittle.** Every check is code over the console state or normalised text (case, commas, `$`, markdown,
  quotes and dashes ignored; any listed form counts). No exact-string grading of prose. Invalid cases (provider
  failure, a developer wording that leaks an answer) are excluded, not failed.
- **No leakage.** `/tests` arrives only at verification and is made root-only before graded agents run; hidden
  data, the R1 overlay and console internals are root-only in the image; during the build only the
  practice-model container holds the provider key. The builder traces were audited: no hidden path, value or R1
  record reached the builder before R1. An earlier grading-time leak (task folder world-readable) was found and
  closed; no workspace had used it.
- **The simulated developer is fair.** Audits of the oracle's and run 5's 105 graded conversations each: no
  invented values, no team or rule names, no chat answers, endings always match the console.
- **Net-new.** The scenario, rules, console, cases, runtime and verifier were written for this task. The only
  external content is the MIT background corpus and public libraries (`README.md`, *Provenance*).

## 6. Calibration history (real evidence)

| Change | Evidence | Why |
|---|---|---|
| Rewrote 4 cases | an oracle pass failed them for the case's fault | a variant added a legitimate request; a follow-up made the reference agent hedge 4/4; a claim was defensibly verifiable; a variant repeated a forbidden value |
| Renamed `req_pb402` | static audit | it would have satisfied "402" by accident; a test now forbids it |
| Added the conversation clock | run 4 lost a case to the host clock | time rules must use the conversation's time |
| Added practice conversations (5 per release) | run 3 failed on its own 200-character validator | the builder needs a way to test the agent it ships |
| Sealed the grader | audit | graded agents could read the task folder |
| Moved grading into Harbor; Q01 on all 30 cases | submission | the trial's reward must come from `tests/test.sh` |
| Rewrote a0-02-C | two independent agents (an oracle run, Opus run 6) routed it to Billing Ops | its "conflict" was a $980 figure from the customer's own dashboard, which fits the routing row for invoice/export mismatches: ambiguous, and not a refutable claim. Now a false policy claim ("requests that time out on our side aren't billed") that the published retries page refutes; the oracle's A0 passes it 3/3 |
| Pinned `tenacity` | an oracle conversation crashed when a call was retried | LiteLLM imports it only to retry (e.g. after a 429); without it the support agent crashed and the case counted as failed (1 of 450 graded conversations, a Q01 case). A test now requires it in the lock |
| Stated duplicate-over-refund precedence in the oracle's R1 skill | the oracle missed a1-02-T in 2 of 4 runs | the task is unambiguous (the 2027 duplicate page amends routing for duplicates), but the reference skill had a generic "refund → route" step that could win |

Builder results on the final case set: run 5 Q00 0.833, Q10 0.867, Q11 0.90, reward 0. Its failures (detailed in
`RUN_REPORT.md`) recur from runs 1-4 on earlier case sets: the retry-date inference is missed in every build,
the tokenizer gap is escalated instead of explained, and the $500 scope is dropped on maintenance. The oracle
missed 0, 2 and 1 cases in its last three runs, a different case each time (variance in the support model).

The gate (Q00 ≥ 0.9, Q11 ≥ 0.9, Q10 ≥ Q00 − 0.1) allows three misses in 30 and one case of regression slack.

## 7. Reproducibility caveats

- **A model provider for the deployed support model.** `SUPPORT_MODEL` and `USER_MODEL` are LiteLLM ids
  (`openai/...`, `anthropic/...`, `openrouter/...`, or an Azure `/openai/v1` endpoint via `OPENAI_BASE_URL`). The
  reported runs use gpt-6-luna on Azure AI Foundry. Results with another support model will differ; the
  builder's failure (rules never written into the agent) should not.
- **Rate limits.** Grading is 120 conversations of about 7 calls each. At 4 workers it needs about 1M tokens per
  minute; at 2 workers about 500K. Below that, calls retry and, if they still fail, cases are marked invalid.
- **Time.** About 3 h of building plus 1-2 h of grading inside the last step's verifier
  (`timeout_sec = 10800`).
- **Run-to-run variance.** The support model is sampled, so a case can flip between runs (the oracle has missed one
  different case in two of its last three runs). The gate's slack absorbs one or two such flips; a score is one
  sample.
- **Build inputs that are not pinned.** The base image is pinned by digest and Python packages by
  `requirements.lock`, but Debian packages (`nodejs`, `npm`, `libfaketime` and a few tools) come from the
  distribution at build time, and the embedding model's weights download from Hugging Face during the build.
- **Q10's console is newer than its conversations.** Q10 replays R0 conversations (dated September 2026) against
  the R1 console, which also holds 2027 invoices. An agent that guesses "the latest invoice" instead of asking
  for the month is caught in Q10 but can get lucky in Q00; asking always passes.
- **Platform.** Built and run on Apple Silicon with Colima; the image is Linux and uses only
  multi-arch base layers and Python wheels. libfaketime is required on Linux and checked at runtime.

## 8. Economic viability

- **Builder:** run 5 used $17.21 and run 6's first step $5.49 of the $30 cap (Claude Code's own cost meter), on
  a Claude subscription via `CLAUDE_CODE_OAUTH_TOKEN`; the meter reports the API-equivalent cost.
- **Grading:** 120 conversations × about 117K input and 3K output tokens. At published gpt-6-luna prices
  that is about $1.5-2 per trial, less with prompt caching. A Claude-class support model would cost roughly an
  order of magnitude more, which is why the reported runs use Luna (disclosed in `RUN_REPORT.md`).
- **Why it is worth running.** Support agents grounded in company knowledge are among the most deployed LLM
  applications, and their running cost is upkeep: pricing, policy and data formats change, and someone has to
  update the agent without breaking it. A wrong answer here is a real cost (an unneeded engineering ticket, a
  credit over the approval limit, a wrong retry date given to a customer). The task measures that upkeep
  directly, with a cost per trial that allows repeated runs.
