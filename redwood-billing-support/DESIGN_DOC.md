# Design Doc: redwood-billing-support

`Task.md` is the exhaustive spec (every rule, case, check and decision). This document is the reviewer's version:
what the task is, why it is built this way, where it is hard, and why it is fair.

## 1. What this task is

The model under test (the builder, Claude Opus 5.5 in Claude Code) is the engineer who owns Redwood Inference's
billing-support agent. Redwood is a fictional LLM-inference company; its knowledge arrives as a raw export of
3,524 records from Slack, Gmail, Jira, Linear, Confluence, Google Drive, HubSpot, Fireflies and GitHub. Most
records have nothing to do with billing, and the billing records mix published policy with drafts, superseded
pages, chatter and look-alikes.

- **Step 1, `build_r0` (2 h):** read the export, decide what to keep, and ship a working support agent in
  `/app/workspace`: a retrieval index (LanceDB, local embeddings), tools over a billing console, at least one
  skill, one subagent and one guardrail hook. Practice conversations on the real deployed model are allowed,
  5 per release.
- **Step 2, `maintain_r1` (1 h, same Claude Code session):** six months later the export grows by 3,547 records,
  three billing pages are revised *in place* (same path, later date), and the console changes its record format.
  Update the agent without breaking what it already did.

The deliverable is the agent itself. It is frozen, then talks to a scripted developer in 120 graded
conversations against a stateful billing console. The verifier reads the console's end state and the
conversation; there is no LLM judge and no report to grade.

## 2. Why this design (methodology lineage)

Method is borrowed; content is not.

- **τ-bench-style grading** (Sierra, public): a simulated user, a tool-backed database, and grading on the
  database end state plus required statements, instead of judging a transcript.
- **Code-scripted user.** The developer's facts, consent answer and endings are code; a model only rewords each
  scripted line, and a wording that drops a number falls back to the script. This removes the main source of
  noise and leakage in model-played users.
- **Agent-building as the task**, not answering. Most RAG and support benchmarks hand the model a fixed retriever
  and grade its answers. Here the model must build the retriever, the tools and the procedures, and then
  maintain them. That is the job companies actually pay for.
- **Background corpus:** EnterpriseRAG-Bench's generated company data (MIT), split into R0 and R1. Every billing
  rule, the console, its data, the 60 cases and all planted records were written for this task
  (`tests/data/provenance.json` names each planted record).

## 3. The core capability under test

Not "can the model build a RAG agent" (it can). The target is **whether the builder turns what it retrieves into
behaviour the agent reliably acts on, and later changes exactly what changed and nothing else.** That needs:

1. **Forensics over a noisy corpus.** Find which records state current policy, by owner, status and date, among
   drafts, superseded pages and decoys.
2. **Operationalising.** Convert a rule into something the deployed agent does every time: a skill step, a
   check, a tool behaviour. A rule that only sits in the index is retrieved and then ignored.
3. **Scoped maintenance.** Find what R1 changed (including in-place revisions a path diff misses), update only
   that, and keep R0 behaviour that still applies.
4. **Verification.** Use the practice conversations to test the hard rules, not just the examples provided.

## 4. The designed traps

All of these are in the shipped content (`Task.md` lists each record):

1. **Scattered rules.** No single record states the whole rule. The duplicate-credit limit lives only in
   Finance's approvals matrix, which the credits page names; in R1 the matrix sets $250 for invoices rated on
   the 2027 price book and keeps $500 for earlier ones, and only Finance's close calendar says the 2027 price
   book starts on 1 March 2027.
2. **Changed in place.** R1 revises the routing page, the failed-payments page and the approvals matrix at their
   R0 paths. A builder that diffs file paths, or keeps its R0 index, misses them.
3. **Date-scoped changes.** The usage-delay threshold becomes 60 minutes only after a Telemetry cut-over that the
   routing page names (ENG-9120) and only that Linear ticket dates. R0 cases in September 2026 still need 30
   minutes, so replacing 30 with 60 everywhere is a regression (Q10).
4. **Inference, not lookup.** The payment-retry date after a processor incident is stated nowhere; four past
   status cards fix it uniquely (retries complete 3 business days after the incident window ends).
5. **A breaking console change** (stale cases). R1 exposes money as `{value, currency}` objects, renames the
   account field on writable tables, adds `billing_timezone`, and requires an escalation priority. A0's tools
   break even where the policy did not change.
6. **Decoys on every platform.** A draft price-book plan in Drive, agents' "everything is urgent" chatter in
   Slack, a provisional Finance x Support meeting, a superseded 2024 thread letting Support grant goodwill under
   $200, and 14 look-alike pages in the same spaces. None states a competing current version of a scored rule.
7. **Twins.** For each release, five cases have a near-identical twin whose correct result is the opposite
   (only 20 minutes have passed, a self-serve account so no SLA claim, exactly at the $250 limit). Pattern
   matching on the ask fails one half.
8. **Developer behaviour.** Each non-twin case has three variants: the developer omits the deciding facts
   (missing_info), is ambiguous, or asserts something false (conflict). Facts come only through the
   `ask_developer` tool, never in chat.
9. **Guardrails that matter.** Credits need the developer's consent first (checked by order), card digits must
   never be repeated (`never_say`), and handoffs must go to the team current policy names.
10. **The clock.** Each conversation runs on its own date and time (libfaketime). An agent that reads
    `date.today()` sees the developer's day, so time-window rules work, and a host clock cannot leak in.

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
