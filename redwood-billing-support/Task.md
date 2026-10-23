# Task: build and maintain Redwood billing support

Status: Draft — task version 1.1.0 (2026-10-23); Harbor 0.22.0. 1.1.0 adds 30 behaviour variants (60 cases),
practice conversations on the deployed support model, the conversation clock, and per-case invalid marking; it
removes the placement diagnostics. Where an older paragraph below disagrees with these, this header and the
sections it names win.

## Purpose and boundary

The builder acts as the developer responsible for a billing-support service.
It inspects organizational knowledge R, chooses its own knowledge representation
and retrieval architecture, implements a deployable agent, and maintains that
agent when R grows. This tests useful developer judgment and implementation,
not compliance with a prescribed hybrid recipe.

The corpus is `environment/corpus` (R0, R1), a slice of Redwood called mini.
R0 contains 3524 source records. R1 adds 3547 and revises 3 R0 records in place
(same path, later `last_updated`; `advance.py` accepts only such revisions): the
billing routing page, the 2026 failed-payments page and Finance's approvals matrix.
The build begins with R0 at `/app/R`. After the first evaluation, the existing
root-only transition reveals R1 in the same container. The builder continues
its same builder session and updates its existing workspace. R2 remains absent.
R1 is the enterprise evolution ΔR. It adds the R1-only `credit_ledger` table
(`data/db_r1.json`, merged over `data/db.json` only in R1), an R1 record format
(money as `{value, currency}`, a case `priority`, `billing_account_id` on the
writable tables, and an account `billing_timezone`), and planted records. The
scored rules are S0 and S1, ten each, with one normal case per rule plus five twins and fifteen behaviour
variants per release (see *Twins, behaviour variants and difficulty*):

| S0 rule (case) | S1 rule (case, kind) |
|---|---|
| BILL-1 tokenizer counting (a0-01) | BILL-CREDIT: $250 from March 2027 usage, $500 before (a1-01, a1-02, changed in place, scattered) |
| BILL-3 retries billed (a0-02) | BILL-SUPERSEDED with v2 current (a1-02, stale) |
| BILL-4 30-minute telemetry (a0-03) | BILL-VERIFY: billing month in `billing_timezone` (a1-05, changed, scattered) |
| BILL-6 contract SLA claim to account_team (a0-04) | BILL-4: 60 minutes from March 2027, a Slack decoy says 10 (a1-06, changed in place) |
| BILL-7 invoice mismatch to billing_ops (a0-05) | BILL-7, R1 write format (a1-04, stale) |
| BILL-9 goodwill to finance (a0-06) | BILL-9, R1 write format (a1-07, stale) |
| BILL-CREDIT in-limit credit (a0-07) | BILL-INCIDENT: no case; retry date only from status cards (a1-03, changed, scattered) |
| BILL-CREDIT over limit + BILL-SUPERSEDED old help page (a0-08) | BILL-LEDGER (a1-08, changed) |
| BILL-ROUTE unexplained meter rows to billing_engineering (a0-09) | BILL-PCI (a1-09, removed: the card-update procedure is withdrawn in place) |
| BILL-VERIFY look up before quoting (a0-10) | BILL-10: 402 on Free (a1-10, changed) |

*Changed* means the rule text changed; *stale* means it didn't, but the R1
console breaks A0's implementation. *Changed in place* means R1 revises the
R0 record at its own path, so a builder that only diffs file paths, or keeps its
R0 index, misses it. *Removed* means the revision withdraws an R0 procedure.
*Scattered* means no single record states the whole rule and each piece links to
the next by key or title in its text (never by a `related_pages` pointer alone):
the duplicate-credit limit lives only in Finance's Billing Adjustments Approval
Matrix (in R0 too: the credits page names the matrix, the matrix gives $500); in
R1 the matrix sets $250 for invoices rated on the 2027 price book and $500 for
earlier ones, and only Finance's close calendar says the 2027 price book rates
usage from 1 March 2027. The 60-minute threshold applies after a Telemetry
cut-over that the routing page names as ENG-9120; only that Linear ticket dates
it (2027-03-01 00:00 UTC), and the date also decides Q10's a0-03 (September 2026,
30 minutes). The Free-tier quota error is named on the pricing page, and only
the API Error Reference (2027) in the developer-experience docs gives its code
(`402 quota_exceeded`; `429` is the per-minute rate limit). The billing-month
rule lives in Finance's close calendar (the format page only defines the
field), and the retry date only in past status cards.

Rules sit with their owners, on several platforms: the approvals matrix (R0,
revised in place in R1) and the 2027 close calendar are `final` Finance docs in
Google Drive; the escalation priority (`normal`) is Dev Patel's #support
announcement of 2027-03-30, which the format page (eng-platform) names; which
table balances come from is the recorded decision of the 2027-03-29 Finance x
Support sync in Fireflies, which the ledger page (Finance) names; the charter is
under policies-and-process, the processor-incident procedure under Finance, the
card-data standard under Security. Of 31 rule-carrying records, 15 (48%) sit in
the support-process, status-page or pricing folders (was 71%). A record outside
Confluence carries part of a rule only when it comes from the rule's owner in a
binding form (a `final` doc, the owner's announcement, a recorded decision, a
ticket closed by the owning team), no published page contradicts it, and a page
the builder reads names it by key, title, or channel and date. Each such
platform also holds a same-kind decoy: a draft price-book plan in Drive, agents'
"everything urgent" chatter in Slack, and an earlier provisional Finance x
Support meeting. INCIDENT, LEDGER, PCI and BILL-10 each replace an R0 page
planted as their stale counterpart (failed card payments and card updates 2026,
credit balances 2026, free-tier limits 2025); no S0 case touches those topics,
so they never decide an R0 answer. Newer pages carry effective dates rather than
"this replaces X" statements; the older page is identifiable by its earlier
date and the newer page's stated effective date. Fourteen look-alike pages
(11 in R1, 3 in R0: partner MDF credits, conversation merging, unrelated status
cards, a dunning schedule, batch pricing and similar) share the planted pages'
spaces, length and dates but cover other jobs; none states a competing version
of a scored rule. Unchanged behavior is measured by replaying the 30
R0 scenarios on R1 (Q10); every R1 edit has a March 2027 cutoff that the R0
cases (August-September 2026, dates stated) fall before.

Every scenario rule (`BILL-*`) is defined in `tests/data/provenance.json` with the
builder-visible records that state it in each release; expected answers are
derived from those records and the release's console data, not from the S spec
that generated R. `test_every_scenario_rule_cites_records_visible_in_its_release`
enforces that each rule a scenario names has a carrier record visible in that
scenario's release.

The support agent can read only its frozen workspace. The builder therefore
chooses what knowledge to ship, its representation, and how the runtime finds
and applies it. An index is optional. At least one functional skill and one
functional subagent, plus a functional hook guardrail, are required for
evaluating the builder's use of these capabilities. Their roles and integration
are design choices.
`agent.py`/`create_agent()` and `tools.py` remain required interfaces.

## Relevant Harness and resources

- Builder: Harbor `claude-code`, `claude-opus-5-5`, effort `high`
  (`run-config.claude.json`); the same session resumes across steps. The
  verifier also accepts a Pi session, which the oracle writes.
- Deployed agent: the run's `SUPPORT_MODEL` (a LiteLLM id, default `openai/gpt-6-luna`, reasoning effort
  `SUPPORT_REASONING_EFFORT`, default high). The image writes it into the manifest and the starter agent, and the
  instructions name the parameter; practice and grading serve it through `tests/run/gateway.py`, so the builder
  practises on the model that is graded. Embeddings:
  `sentence-transformers/all-MiniLM-L6-v2` run locally (fastembed/ONNX, weights baked into the image).
- Developer: a script per scenario (`customer` in `tests/data/billing_scenarios.json`): code decides the order of
  lines, the facts served through `ask_developer`, the consent answer through `confirm_with_developer`, and the
  endings; `USER_MODEL` (default `openai/gpt-6-luna`) only words each scripted line (`redwood_kit.customer`) and never
  sees the facts. A wording that drops a number or identifier falls back to the line as written.
- Builder limits: R0 7200 seconds, R1 3600 seconds; 2 CPUs, 8192 MiB memory,
  20480 MiB storage. Network: Anthropic hosts for the builder's harness, plus the support model's provider host,
  which only the `practice-model` container can use (it alone holds the key).
- Practice conversations: `python -m redwood_kit.practice SCRIPTS.json` runs the builder's workspace through
  `redwood_kit.conversation`, the loop graded conversations use (same greeting, protocol, tool rounds, 40-turn
  limit, resource limits, frozen copy), with the support model via the practice-model service, a fresh practice
  console and a scripted developer worded by the same rules. Example scripts cover the practice accounts only
  (`framework/client_api/practice_conversations.json`); the builder can write its own. 5 practice conversations
  per release, each with up to 60 support-model calls and 40 wordings, enforced by the service; calls are logged
  without content.
- Clock: the support process runs on the conversation's date and time (UTC) through libfaketime
  (`redwood_kit.conversation.clock_env`): each case's brief `today` plus its `now` (12:00 unless its facts give
  a time), and each release's practice date. A builder that reads `date.today()` sees the developer's day.
- Runtime: frozen read-only workspace, no direct external network or credentials,
  gateway inference, 4 GiB address space and 128 MiB per-file write limit.
  At most 40 agent turns per conversation, including tool rounds. Each wait
  for candidate output has a 180-second timeout, distinct from gateway
  provider timeouts. `build_r0`'s verifier is bounded at 3600 seconds; `maintain_r1`'s, which grades, at 10800.
- No practice judge: retrieval practice questions are read by the builder, not scored.

## Builder engineering references

All builders receive offline RAG reference pack 1.1.0 under
`/app/framework/offline-rag/`, identically in R0 and R1. It contains generic
architecture options, corpus-inspection guidance, chunking/retrieval tradeoffs,
synthetic runnable SQLite and embedding-interface examples, and testing/update
guidance. It contains no company-policy answers, source-ranking hints, or
hidden evaluation truth. Its linked primary sources provide attribution; the
pack is self-contained without web search. The examples are optional and do
not choose or prebuild an index over R.

The pack version is declared in its README, architecture guide, and executable
example. Task version 0.3.0 introduced this RAG pack; earlier task runs without
the pack are not equivalent.
The existing Docker `COPY environment/app/ /app/` distributes it read-only.
Runtime code can reuse selected examples only by shipping them in the workspace.
Required skills, subagents, and hooks remain required; architecture is free.

Task version 0.4.0 adds offline agent engineering reference 1.0.0 as
`/app/framework/agent_building.md` and its `agent_examples.py` self-check,
identically in R0 and R1. The reference covers architecture choices, component
interfaces, state/context, tools, skills, bounded delegation, hooks, evidence
investigation, recovery, testing, and maintenance. It cites primary engineering
sources and pinned Evo-Bench code, adapting their ideas to the installed kit.
Examples use synthetic data and a scripted gateway without API calls.
The runtime contract and manifest remain authoritative. This is a changed
builder information condition, not a new reward, required architecture, or
public conversation-scoring service. No company policy, practice answers,
hidden scenario details, or calibration results are included in this reference.
The same Docker copy distributes it read-only; no packaging dependency is added.

## Exact builder inputs

### build_r0

```markdown
# Build Redwood Inference's Billing Support Agent

Redwood Inference sells LLM inference: a hosted API, SDKs, and private
deployments. Build the agent that answers developers about pricing and plans,
token usage, invoices, credits, and refunds.

## The domain

The support agent serves Redwood's customers, developers and companies using
its models, on billing questions about their own accounts. That covers what a
customer is charged and why, what they can do about a charge, and what Support
itself may do or must pass to someone else.

## Materials and deliverable

`/app/R/` is a read-only export of Redwood's organizational knowledge from
Slack, Gmail, Jira, Linear, Confluence, Google Drive, HubSpot, Fireflies, and
GitHub, plus company files. One JSON file is one source record. The material
includes unrelated information, proposals, historical decisions, and conflicts.

`/app/framework/agent_contract.md` describes the runtime interface and limits.
`/app/framework/agent_building.md` provides optional engineering guidance for
architecture, tools, state, skills, subagents, guardrails, testing, and maintenance,
with a runnable synthetic self-check. Consult it when designing or debugging
agent components; the runtime contract remains authoritative.
`/app/framework/offline-rag/README.md` and
`/app/framework/offline-rag/architectures.md` describe retrieval architectures
and practice questions you can run through your retrieval to see
how well it is finding evidence; you read the results and judge them.
`/app/workspace/` contains a minimal starting agent. Deliver a working agent
there, using `agent.py` and `tools.py` as the evaluator's entry points. You may
change the starter and add files. Deliver at least one functional skill and
one functional subagent for the billing-support job. Replace or remove the
example stubs. Choose their responsibilities and integrate them into the
agent so it can load the skill and delegate to the subagent when appropriate.
A placeholder file or an unreachable component does not satisfy this requirement.
Also implement at least one functional `@hook` guardrail for this job.

Work like the developer responsible for this service: inspect R, decide what
knowledge to retain and how to organize it, explore retrieval architectures,
implement the one you ship, and test it.

`system_prompt.md` is identity: who the agent is and whose interests it
serves. `AGENTS.md` is operating principles: how it retrieves, checks
evidence, and hands off. A skill is a procedure for doing that work. Keep
`system_prompt.md` under 1500 characters and `AGENTS.md` under 4000
characters. Exact billing rules do not go in those files. They are read from
retrieved chunks at conversation time. Do not keep a store of full source
records, and do not give the agent a tool that reads one back.

Explore the architectures in `/app/framework/offline-rag/architectures.md`
and ship the design that finds those chunks during a conversation.
The index is LanceDB: put chunk text and the gateway vectors in one
table, and search with a vector, with text, or with both.

Dense embeddings run on this container's CPUs: there is no external embedding
API, and embedding is slow. Do not embed all of `/app/R`. Choose the records
you judge relevant to the billing-support domain and build the dense index
from those only. This limit applies to dense vectors, not to lexical
(full-text) indexing. The support
agent decides from the chunks that search returns. Single-pass search,
retrieve–rerank–read, routed retrieval, iterative search, corrective
checks, graph-assisted reads, summary trees, and contextualized chunks
are the patterns. Lexical, dense, and hybrid search sit inside them.

## Deployment

The evaluator freezes the workspace and runs the support agent from that
read-only copy. It cannot read `/app/R`, install dependencies, or access
external services directly. Retain everything it needs in the workspace.
Model and embedding calls use the gateway and the models in
`framework/deployment_manifest.json`. The builder has 7200 seconds; the
container has 2 CPUs, 8 GiB memory, and 20 GiB storage. Further runtime limits
are in the agent contract.

Each case is a conversation with a developer. The developer answers
questions and approves actions only through the `ask_developer` and
`confirm_with_developer` tools described in the agent contract. The agent
can use its tools before replying. Use Redwood's
materials to give accurate information, distinguish current policy from
proposals and historical facts, ask for missing details, and hand off to the
appropriate team when needed.

## Development feedback

`framework/offline-rag/README.md` includes practice questions you can run
through your retrieval to check its evidence while you change how the agent
finds it; you read the results, nothing scores them. Test the behavior of the agent you actually ship.
Use `redwood_kit.billing.development_billing()` to test console-facing
tools and hooks against release-matched synthetic accounts. The cases are
listed in `/app/framework/client_api/practice_cases.json`; writes stay local
and are not graded.

To try whole conversations, `python -m redwood_kit.practice SCRIPTS.json`
runs your workspace through the same conversation loop the held-out
conversations use: the deployed support model, a fresh practice console, and
a scripted developer whose lines that model words. Example scripts over the
practice cases are in `/app/framework/client_api/practice_conversations.json`;
write your own in the same format. You have 5 practice conversations per
release (`framework/agent_contract.md`). Practice conversations are not graded.

Success is measured on held-out billing-support conversations: correct,
current answers, appropriate clarification, and correct handoffs.
```

### maintain_r1

```markdown
# Maintain Redwood Inference's Billing Support Agent

Continue from your previous session and the workspace you delivered.
`/app/R/` has changed: new records have been added, some existing records have been
revised in place (same path, later update date), and none have been deleted. Review
what changed, then update the support agent's knowledge and implementation.
Newer material may refine or contradict earlier material; determine its scope
and authority rather than assuming that the latest text always wins.

The agent must handle both new situations and the billing questions it already
supports. Test the updates and check for regressions. How you organize,
retrieve, and maintain its knowledge is your choice.

`/app/framework/agent_building.md` includes optional maintenance, component
integration, and regression-testing guidance. It is the same reference as R0.

The runtime interface, allowed models, and workspace-only deployment boundary
are unchanged. Dense embeddings still run on this container's CPUs with no
external embedding API: embed only the records you judge relevant to the
billing-support domain, not all of `/app/R`. You have 3600 seconds in this step. The support agent still
cannot read `/app/R`; all knowledge it needs must ship in the workspace.

Optional practice questions in `framework/retrieval_probes.json` list the
releases they apply to; use the ones for the current release in
`/etc/redwood-release`. What you read from them is advisory and separate from
the held-out billing conversations that determine success.

Use `redwood_kit.billing.development_billing()` to test console-facing
tools and hooks against release-matched synthetic accounts listed in
`framework/client_api/practice_cases.json`. Writes stay local and are not
graded. `python -m redwood_kit.practice SCRIPTS.json` runs whole practice
conversations through the graded conversation loop with the deployed support
model; `framework/client_api/practice_conversations.json` has example scripts
for this release, and R1 has its own 5 practice conversations.
```

## Practice questions

`framework/retrieval_probes.json` lists practice questions (6 billing and 3
general-retrieval at R0; 2 more billing at R1) with the releases they apply to.
There is no scorer and no reference answers: the builder runs each applicable
question through its own retrieval, reads the returned passages and judges
whether the evidence is present, buried, stale or contradicted
(`framework/offline-rag/README.md` has the loop). Nothing here is graded or
scored and no OpenAI credential or judge model is involved. An earlier
revision shipped a RAGAS-based score service with private references; it was
removed because it needed an OpenAI key and its metrics were advisory only.

## Final outcome and independent verification

Required skills, subagents, and hook guardrails must contain substantive job-specific behavior
and be reachable through the deployed agent. Placeholder files do not establish
capability. Audit loading/delegation behavior and the component's effect in the
runtime evidence; there is no requirement to invoke either in every case. The
conversation reward (`reward`) does not score artifact presence. Each step's
`reward.json` reports `artifact_quality`: code checks for the instruction's size
limits, a non-stub skill, subagent and `@hook`, and that `load_skill` and
`delegate` are exposed. It does not change `reward`, and component quality still
needs trace review.

Each hidden case in `tests/data/billing_scenarios.json` is a simulated
conversation (30 per step, 60 in total: 15 base cases and 15 behaviour variants per release), chosen
so that every check is decided by code. In base cases the developer
states a real need once, never pushes, names no team or rule, and holds facts
back until asked; variants change only that behaviour; every ask a check depends on comes before the first turn
where a handoff is correct, because a handoff ends the conversation. A case's
first rule is the one it tests; BILL-CONFIRM is listed second on a1-01 and
a1-02 so the consent check runs. The runtime provides one typed record store with
`describe`, `query`, `insert`, and `update`; its schema is discoverable through
`describe()`. R1 exposes cents as money objects, names the account
`billing_account_id` on the writable tables, adds `billing_timezone` to accounts,
and requires a case priority when escalating. The shared baseline is `data/db.json` (plus `data/db_r1.json` in R1),
copied into the image at root-only `/opt/redwood-data/db.json`; each conversation
gets its release view.
Gold and alternatives are generic insert/update traces replayed from that
baseline. Grading compares projected writable tables, ignoring generated
identifiers and leftover drafts. The action check (hyper-tau's ACTION on
console operations) requires each listed `query:`, `insert:`, `update:`, or
`write:` operation to match a successful call: the account lookup, handoff
facts in the case or the conversation before it, and text no write may store.
Practice retrieval questions are not part of the reward.
- **COMMUNICATE.** Values the agent must tell the developer, derived from the
  console or R (e.g. a1-08's ledger balance and both grant expiry dates); any
  listed form counts, and no value appears in the developer's brief.
- **NEVER_SAY.** Values no agent message may contain: a1-09's card digits, and
  the wrong answer a frozen R0 copy gives (a1-03's calendar-day retry date,
  a1-05's UTC-month token total).
- **No LLM judge.** The assignment rules out a model judging the trajectory, so
  every former judge question was either turned into one of the checks above
  or dropped with its case. A verdict is reproducible from the case log.

The developer's script decides every fact, consent answer and ending, so every run of a case gets the same
substance; only the wording of its lines varies, and wordings are cached for regrading. A wording that gives away
a value the agent must say, before the agent says it, marks the case invalid (`developer_leak`).
- **CONSENT.** Under BILL-CONFIRM, every successful credit note or dispute
  insert must come after a `yes` from `confirm_with_developer`. Gold actions that the backend rejects are a scenario bug
and raise. Gold actions and matchers stay in `tests/`; the builder sees only the
API shape in `framework/agent_contract.md`.

Wrong answers, unsupported policy claims, missing required handoffs, and other
existing scenario failures remain agent failures. Verifier/setup/customer
faults handled by the existing invalid-run paths produce no agent score.
This repair does not claim a comprehensive audit of every existing runtime
failure path. Optional practice-provider failures are explicitly classified.

For evidence inspection, each runtime model call now retains its input
messages and response, including nested model contexts and consumed record
reads. Top-level tool messages, escalated cases, and the billing end state
and call log remain recorded. This does not claim to capture every internal Python operation or unused tool result.

### Twins, behaviour variants and difficulty (60 cases)

Each release adds 15 behaviour variants of its 5 non-twin base cases (R0 a0-01, 02, 06, 07, 10; R1 a1-04, 05, 07,
09, 10), one per behaviour, following observe-bench's customer behaviours: `-M` missing_info (the opening drops the
deciding facts; they come only from the form; their case-01/02), `-A` ambiguous (the opening fits several
readings; the form or console settles it; their case-06/10), `-C` conflict (the developer asserts something the
console or a published rule refutes: a wrong total or plan, a promised refund, a nonexistent counting or refund
term, "give me the card here"; their case-09/11). A variant copies its base's gold exactly (test-enforced), so the
base is its control; every conflict claim was checked false against the corpus (the "tiktoken" claim was replaced
because the model registry names a tiktoken-compatible tokenizer). All 60 cases count toward the reward;
`scores.json` reports robustness separately (below).

Each release has 15 cases: the ten rule cases and five twins. A twin is a similar ask from a different
customer, with its own account, wording and records, whose correct result differs for a reason the agent must
find (a0-03-T only 20 minutes have passed; a0-04-T self-serve so no SLA; a0-05-T the line is correct, a waiver to
finance; a0-08-T under the limit so a credit; a0-09-T the customer counted user messages only; a1-01-T a contract
account so a dispute; a1-02-T exactly the $250 limit; a1-06-T 75 minutes; a1-08-T the grant expired). a1-03-T is
the exception by design: a second inside-window payment failure (dunmore-systems) so the retry-date inference
appears twice per run.

`info_type` follows agent-maintain-bench's `sources`: 1 every deciding fact is in the opening; 2 a deciding fact
comes only from the customer when asked; 3 opening facts plus console lookups; 4 facts given only when asked plus
console lookups. Each release's 15 base cases are 8 / 1 / 5 / 1 (type 4 / 3 / 2 / 1), enforced by a test; variants carry their own type. Type-4 cases need at
least four console operations (account, invoices, lines, usage, ledger or contracts, plus writes), and every extra
question carries its own `communicate` value recomputed from the console in the tests.

The developer answers questions only through `ask_developer`; the model that words the developer's lines never
sees the facts, so a question asked in chat is not answered (as the agent contract says). The contract lists what
each `ask_developer` field means. Extra questions sit in the opening, so a handoff on the first reply cannot hide
them, and openings never use relative dates.

Verifier: `communicate`/`never_say` match after normalising case, commas, `$`, markdown and typographic quotes and
dashes. Accepted alternatives: either duplicate line in R0; a dispute named in the case text instead of the
structured `dispute_id`; an `incorrect_amount` dispute plus the Billing Ops case for an invoice/export mismatch.
An offline audit replays every case: the gold passes, doing nothing fails, and a twin's gold fails its pair
(a1-03-T excepted). Free accounts carry no invoices (they had six months of paid invoices, an environment
defect, removed).

## Fairness, leakage, and production differences

- The task specifies the job and actual runtime boundary; alternative valid
  architectures receive the same outcome evaluation.
- The practice bank has six R0 billing cases and two R1 additions, plus two
  separately grouped general-retrieval cases. It is development data, not a
  statistical certificate of generalization. One draft pricing question is
  explicitly about the proposal; it does not assert current pricing policy.
- Exact-id, paraphrase, draft/current authority, missing deployment evidence,
  historical-case scope, conflicting source terminology, and new R1 evidence
  are represented. The R1 additions test newly available knowledge and an
  internal inconsistency; they do not claim to be a dated policy supersession.
  Actual update/regression outcomes remain covered by hidden conversations.
- Reference evidence may be internally inconsistent. A published label alone
  does not settle a contradictory calculation; an appropriate answer can
  identify the conflict and seek clarification instead of inventing certainty.
- Self-submitted diagnostic chunks are not independent proof that the deployed
  agent retrieved them. Final support outcomes and model-call evidence remain
  the basis for auditing the delivered artifact.
- Every hidden scenario rule cites builder-visible R records per release
  (`tests/data/provenance.json`, test-enforced). A static audit found none of the
  scenarios' invoice or request IDs anywhere in R0, R1 or `environment/app`
  (re-run 2026-10-23; the only account name found, acme-analytics, appears in
  upstream emails that say nothing about billing retries). Every R1 case relies
  on an R1-only carrier record or the R1 console (test-enforced).
- Planted R0 records were checked against all ten S0 cases at Q00 and Q10: none
  changes an S0 answer. The 2024 #support thread saying Support may approve
  goodwill under $200 is kept as a superseded contradicting record for BILL-9;
  the 2026 escalation-paths page's usage-anomaly row now defers billing cases
  to the routing page, so it no longer competes with a0-09's route.
- Scenario DB checks accept every outcome the cited records allow (e.g. a0-07
  and a0-08 accept either duplicated line in R0, where no record names the copy).
- `reward.json` holds only the gate. `scores.json` holds diagnostics that never change it: `q` (pass rates);
  `invalid` (cases whose conversation says nothing about the agent: the provider failed, or the developer gave an
  answer away; left out of the rates, listed for regrading with `--only`); `info` (pass rate by info_type, and
  twin pairs that count only when both halves pass); `behavior` (pass rate per behaviour and of the bases,
  robustness = variant rate / base rate, and each variant against its base: behaviour failure, handled, both
  failed, variant only); `drift` and `regression` per case; `health` per Q (time, model calls, tokens,
  embeddings, developer turns, over all and passing cases, and `ends`: terminations with agent errors split into
  crash or timeout, agent exception, bad message); `sizes` (lines per workspace layer, index bytes).
  Q01 is diagnostic and grades base cases only (`--base-only`).
- Calibration: one attempt per case; 30 cases per step, so one case moves a pass rate by about 0.033. The gate
  allows Q10 one-in-ten slack below Q00.
- References, future overlays, baseline, verifier files, and score logs are
  root-only (checked in the image as the `harbor` user: seeds, console internals, overlays, the practice service
  code and /tests are unreadable; no hidden case identifier exists outside /app/R). The practice-model container
  alone holds the provider key; the builder's container has no provider variables. Practice scripts name only
  practice accounts. The practice socket is root:harbor 0660 and unreachable by the
  support user. Its directory is traversable so builder access works even
  when startup uses a restrictive umask. Safe logs contain hashes and timing,
  not credentials, passages, or hidden answers.
- `Task.md`, the research report, `tests/`, and `solution/` are not copied into
  `/app`. At grading, graded agents run as `nobody` beside the grader, so the verifier is baked root-only into
  `redwood-grader` (`scripts/grader.Dockerfile`), never mounted, and the grader writes with `umask 077` into a
  0700 `/out`. In-Harbor grading (`maintain_r1`'s `test.sh`) does the same: `/tests` arrives only at verification
  and is made root-only before any graded agent runs, and the grader writes with `umask 077`. (Until 2026-10-23 the task folder was mounted world-readable during grading: a build could have
  read the scenarios. None of the 37 saved workspaces references a hidden path.)
- Audit of the final oracle's 105 conversations: the worded developer never added a number or identifier, named a
  team, rule or priority, or answered a question asked in chat (47 of 190 lines fell back to the scripted line);
  every value the agent had to say appears in a tool result except a1-03's retry date, which the four past status
  cards determine uniquely (3 business days after the window ends: a Friday-ending window excludes calendar days,
  a Sunday-starting one excludes counting from the start); transfers happen exactly after escalated cases and
  match the gold. Twin gold swaps fail except a1-03/a1-03-T (by design) and a1-08/a1-08-T (both write nothing;
  their values differ). No checked value hides inside an identifier on its account (a1-10's `req_pb402` would have
  passed "402"; renamed, and a test forbids it).
- This is a frozen simulated company and support environment, with predefined
  allowed models and no direct production services. It does not measure live
  enterprise connector behavior, unlimited infrastructure choice, or discovery
  across unseen companies.

## Running and scoring

```sh
harbor run -y -c redwood-billing-support/run-config.claude.json -o "$PWD/jobs"   # A0, A1, then grading
scripts/grade_snapshots.sh jobs/<job>/<trial>                                    # optional: Q01, regrades
```

`maintain_r1`'s verifier grades Q00, Q10 and Q11 and writes the gate into its `reward.json`
(`multi_step_reward_strategy = "final"`); `build_r0`'s is a placeholder. Runs reported below predate this (graded
by `scripts/grade_snapshots.sh` after the job) and the LiteLLM gateway (then the OpenAI Responses API, same model). The gate, the Q runs and `scores.json`
are described in `tests/README.md`.

Final oracle (job `2026-10-23__20-38-54`, task 1.1.0, 60 cases; support agent and developer wording on
gpt-6-luna; Harbor ran both steps with the practice sidecar and the clock in 7.5 minutes, artifact quality 1.0 on
both): Q00 30/30, Q10 30/30, Q11 30/30, reward 1.0; Q01 1/15 (diagnostic). Every behaviour variant passed
(robustness 1.0 in Q00, Q10, Q11), twins 5/5 and every info type in each gated Q; no case invalid after the
5-case regrade, no developer leak, endings only stops and transfers (11 handoffs per release, as gold). Two
variant claims and one base follow-up were fixed after an earlier oracle pass failed them for the case's fault, not
the agent's: a0-07-C's "refund both lines" added a legitimate Finance request; a0-06-C's claim about the
customer's own contract was defensibly sent for verification (replaced by a false policy claim); a0-02's
follow-up "so there's nothing to fix?" made the reference agent hedge into a review in 4 of 4 runs (now an
informational question); a1-05-C repeated its base's never_say value (now a different figure; a test forbids it).

Builder runs on the earlier case sets (Claude Code, Opus 5.5, high effort): run 3 (`2026-10-23__17-06-10`) reward
0, dominated by its own tool bug (a 200-character summary validator rejected every handoff; practice on run 3's
frozen A0 reproduces it in one conversation); run 4 (`2026-10-23__18-49-44`, the first with practice) fixed that
bug and lost a0-03-T to the host clock, which the clock fix removed. Across runs 1-4 a1-03 failed every time: the
past status cards reach the agent, but no builder writes the rule they imply (retries complete 3 business days
after the incident window ends). Given that rule in its system prompt (`REDWOOD_REPLAY_HINT`), the agent gives
April 12 on a1-03 (the rule was missing, not unusable) and still says April 10, a Saturday, on a1-03-T (it cannot
apply it). a0-01 (and a0-09-T, run 4) opened a Billing Engineering case for a token gap the published pages
explain in every run.

Run 5 (`2026-10-23__20-54-44`, the final task: 60 cases, 5 practice conversations per release, clock, sealed
grader; $17.21 of the $30 cap): Q00 0.833, Q10 0.867, Q11 0.90, reward 0; no invalid case after a one-worker regrade
of six rate-limited ones; robustness 1.0+ (no behaviour failure: every failing variant's base failed too); drift 12
R1 cases absorbed; regression a0-08. Its builder trace was audited: no hidden path, value or R1 record reached it.

**Failure mode (genuine, recurring, fair): retrieved but not operationalised.** In every recurring failure the
deciding record reached the support agent (checked in run 5's tool results), yet the build never turned it into a
decision rule the agent acts on, so the agent fell back to a cautious default: escalate, stay vague, or decline to
give a date. The oracle, which writes each rule into a skill, passes all of them.
- a1-03 / a1-03-T, retry date from past status cards (an inference with one answer: the cards fix business days
  and the window's end): failed in every graded build (runs 1, 2, 3, 5; run 4's build also wrote no rule). Given the
  rule in its system prompt the agent answers a1-03 (knowledge missing, not unusable).
- a0-01 family and a0-09-T, "check the meter": the current routing page says to answer and not hand off unless
  request IDs still mismatch after the published rules; the only contrary record is a superseded draft that names
  Telemetry. Escalated to Billing Engineering in Q10 in all five runs and in Q00 in four (run 3 passed only because
  its own tool bug blocked the handoff); in run 5 the base and all three behaviour variants fail alike.
- a0-08 on A1 (Q10), a maintenance scoping regression: R1's approvals matrix keeps $500 for invoices on earlier
  price books and sets $250 for the 2027 one; A0 states the $500 limit in every run, A1 dropped it in four of five.

Local checks need no model credentials: `dev/tests` (pytest) and
`scripts/check_redwood.sh` (ruff, strict mypy, xenon). One run validates the
plumbing; it does not establish reliability. Keep Status: Draft until repeated
runs calibrate the gate.
