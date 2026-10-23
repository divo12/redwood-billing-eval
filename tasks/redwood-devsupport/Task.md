# Task: build and maintain Redwood billing support

Status: Draft — task version 1.0.0 (2026-10-21); Harbor 0.22.0. The 1.0.0
changes (scripted developer, support agent on Claude, Claude Code builder,
multi-criterion reward, packaging) supersede the older statements kept below
as design history; `RUN_REPORT.md` holds the current results.

## Purpose and boundary

The builder acts as the developer responsible for a billing-support service.
It inspects organizational knowledge R, chooses its own knowledge representation
and retrieval architecture, implements a deployable agent, and maintains that
agent when R grows. This tests useful developer judgment and implementation,
not compliance with a prescribed hybrid recipe.

The active corpus is `environments/redwood-mini`, not nano or full Redwood.
R0 contains 3520 source records; R1 adds 3536 without replacing R0 paths.
The build begins with R0 at `/app/R`. After the first evaluation, the existing
root-only transition reveals R1 in the same container. The builder continues
its same builder session and updates its existing workspace. R2 remains absent.
R1 is the enterprise evolution ΔR. It adds the R1-only `credit_ledger` table
(`data/db_r1.json`, merged over `data/db.json` only in R1), an R1 record format
(money as `{value, currency}`, a case `priority`, `billing_account_id` on the
writable tables, and an account `billing_timezone`), and planted records. The
scored rules are S0 and S1, ten each, with one normal case per rule:

| S0 rule (case) | S1 rule (case, kind) |
|---|---|
| BILL-1 tokenizer counting (a0-01) | BILL-CREDIT: $250 from March 2027 usage, $500 before (a1-01, a1-02, changed) |
| BILL-3 retries billed (a0-02) | BILL-SUPERSEDED with v2 current (a1-02, stale) |
| BILL-4 30-minute telemetry (a0-03) | BILL-VERIFY: billing month in `billing_timezone` (a1-05, changed) |
| BILL-6 contract SLA claim to account_team (a0-04) | BILL-4: 60 minutes from March 2027, a Slack decoy says 10 (a1-06, changed) |
| BILL-7 invoice mismatch to billing_ops (a0-05) | BILL-7, R1 write format (a1-04, stale) |
| BILL-9 goodwill to finance (a0-06) | BILL-9, R1 write format (a1-07, stale) |
| BILL-CREDIT in-limit credit (a0-07) | BILL-INCIDENT: no case; retry date only from status cards (a1-03, changed) |
| BILL-CREDIT over limit + BILL-SUPERSEDED old help page (a0-08) | BILL-LEDGER (a1-08, changed) |
| BILL-ROUTE unexplained meter rows to billing_engineering (a0-09) | BILL-PCI (a1-09, changed) |
| BILL-VERIFY look up before quoting (a0-10) | BILL-10: 402 on Free (a1-10, changed) |

*Changed* means the rule text changed; *stale* means it didn't, but the R1
console breaks A0's implementation. INCIDENT, LEDGER, PCI and BILL-10 each
replace an R0 page planted as their stale counterpart (failed card payments and
card updates 2026, credit balances 2026, free-tier limits 2025); no S0 case
touches those topics, so they never decide an R0 answer. Every R1 replacement
names the page it replaces. Unchanged behavior is measured by replaying the 10
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
- Deployed agent: `claude-sonnet-5-5` with prompt caching, no extended thinking, enforced by
  the deployment manifest and the Anthropic gateway in `tests/run/gateway.py`.
  Embeddings: `sentence-transformers/all-MiniLM-L6-v2` run locally (fastembed/ONNX, weights baked into the image,
  ~28 texts/s on 2 CPUs); no OpenAI key or host anywhere.
- Developer: a fixed script per scenario (`customer` in
  `tests/data/billing_scenarios.json`), no model. Facts come only through the
  `ask_developer` tool and consent through `confirm_with_developer`.
- Builder limits: R0 7200 seconds, R1 3600 seconds; 2 CPUs, 8192 MiB memory,
  20480 MiB storage. Network: Anthropic hosts for the builder's harness and
  nothing else.
- Runtime: frozen read-only workspace, no direct external network or credentials,
  gateway inference, 4 GiB address space and 128 MiB per-file write limit.
  At most 40 agent turns per conversation, including tool rounds. Each wait
  for candidate output has a 180-second timeout, distinct from gateway
  provider timeouts. Each complete verifier step is bounded at 3600 seconds.
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
`/app/framework/offline-rag/README.md` provides optional offline engineering
references, RAG architecture types, and runnable examples. Consult it as needed;
you choose which ideas to use.
`/app/workspace/` contains a minimal starting agent. Deliver a working agent
there, using `agent.py` and `tools.py` as the evaluator's entry points. You may
change the starter and add files. Deliver at least one functional skill and
one functional subagent for the billing-support job. Replace or remove the
example stubs. Choose their responsibilities and integrate them into the
agent so it can load the skill and delegate to the subagent when appropriate.
A placeholder file or an unreachable component does not satisfy this requirement.
Also implement at least one functional `@hook` guardrail for this job.

Work like the developer responsible for this service: inspect R, decide what
knowledge to retain and how to organize it, choose an architecture, implement
it, and test it. Indexing, retrieval, stored policies, query planning, and
follow-up reads are your design choices. Choose complexity that serves the
billing job and fits the deployment limits.

## Deployment

The evaluator freezes the workspace and runs the support agent from that
read-only copy. It cannot read `/app/R`, install dependencies, or access
external services directly. Retain everything it needs in the workspace.
Model and embedding calls use the gateway and the models in
`framework/deployment_manifest.json`. The builder has 7200 seconds; the
container has 2 CPUs, 8 GiB memory, and 20 GiB storage. Further runtime limits
are in the agent contract.

Each case is a conversation with a developer who may answer follow-up
questions. The agent can use its tools before replying. Use Redwood's
materials to give accurate information, distinguish current policy from
proposals and historical facts, ask for missing details, and hand off to the
appropriate team when needed.

## Development feedback

`framework/offline-rag/README.md` documents available retrieval facilities and optional
practice checks. You choose whether to use them and which designs to compare.
Their scores are diagnostic; no architecture or practice-score threshold is
required. Test the behavior of the agent you actually ship.

`/app/framework/client_api/schema.json` is the deployment's console contract,
and `/app/framework/client_api/practice_cases.json` lists synthetic billing
cases. Use `redwood_kit.billing.development_billing()` for release-matched
local console testing; writes stay local and are not graded.

Success is measured on held-out billing-support conversations: correct,
current answers, appropriate clarification, and correct handoffs.
```

### maintain_r1

```markdown
# Maintain Redwood Inference's Billing Support Agent

Continue from your previous session and the workspace you delivered.
`/app/R/` has grown: new records have been added and nothing removed. Review
what changed, then update the support agent's knowledge and implementation.
Newer material may refine or contradict earlier material; determine its scope
and authority rather than assuming that the latest text always wins.

The agent must handle both new situations and the billing questions it already
supports. Test the updates and check for regressions. How you organize,
retrieve, and maintain its knowledge is your choice.

`/app/framework/agent_building.md` includes optional maintenance, component
integration, and regression-testing guidance. It is the same reference as R0.

The runtime interface, allowed models, and workspace-only deployment boundary
are unchanged. You have 3600 seconds in this step. The support agent still
cannot read `/app/R`; all knowledge it needs must ship in the workspace.

Optional practice questions in `framework/retrieval_probes.json` list the
releases they apply to; use the ones for the current release in
`/etc/redwood-release`. What you read from them is advisory and separate from
the held-out billing conversations that determine success.
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
conversation (10 per step, 20 in total: a0-01 to a0-10, a1-01 to a1-10), chosen
so that every check is decided by code. Every case is normal: the developer
states a real need once, never pushes, names no team or rule, and holds facts
back until asked; every ask a check depends on comes before the first turn
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

The developer is a script, so it cannot fail or drift: every run of a case
gets identical developer turns, facts and consent answers.
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
- Drift, regression, health and workspace sizes are diagnostics in
  `scores.json` (`tests/evolve/diagnostics.py`); they never change the reward.
  Runtime cost is reported (tokens, calls, seconds per conversation), not penalised.
- Calibration is thin: one oracle run and one Opus 5.5 build have been graded,
  with one attempt per case. Ten cases per step means one case moves a pass rate
  by 0.1.
- References, future overlays, baseline, verifier files, and score logs are
  root-only. The practice socket is root:harbor 0660 and unreachable by the
  support user. Its directory is traversable so builder access works even
  when startup uses a restrictive umask. Safe logs contain hashes and timing,
  not credentials, passages, or hidden answers.
- `Task.md`, the research report, `tests/`, and `solution/` are not copied into
  `/app`.
- This is a frozen simulated company and support environment, with predefined
  allowed models and no direct production services. It does not measure live
  enterprise connector behavior, unlimited infrastructure choice, or discovery
  across unseen companies.

## Running and scoring

```sh
harbor run -y -c tasks/redwood-devsupport/run-config.claude.json -o "$PWD/jobs"   # builder: A0, then A1
scripts/grade_snapshots.sh jobs/<job>/<trial>                                      # Q00, Q01, Q10, Q11 + reward
```

Harbor's per-step `reward.json` is a placeholder (`graded: false`); the run's
reward is `<trial>/graded/reward.json`. The gate, the Q runs and `scores.json`
are described in `tests/README.md`.

Oracle (2026-10-23, the current 20 cases, support agent and customer wording on gpt-6-luna,
`REDWOOD_CUSTOMER_MODEL=gpt-6-luna`): Q00 1.0, Q01 0.1, Q10 1.0, Q11 1.0, reward 1.0. Audit of all 40
conversations: no customer message contained an expected value, a team name or "dispute"; every value
the agent stated came from a tool result or was computed from them (a1-08's $250 sum, a1-03's retry
date from the four status cards); every handoff quoted a retrieved rule naming its team. A0's one Q01
pass (a1-05) used the R1 console's visible `billing_timezone`, so that case separates A0 from A1 only
weakly. Two runs before this one found case and oracle bugs that are fixed: months named without a year
(the agent is never told the date), a0-09's wording not matching its routing row, the oracle not linking
its dispute, and the customer ignoring approval questions asked in chat (it now answers them; consent
still counts only through `confirm_with_developer`).

The developer can be worded by a model: with `REDWOOD_CUSTOMER_MODEL` set, `run/written_customer.py`
rewords each scripted line as a reply to the agent (baseline customer rules, no behaviour persona), while
code keeps the order of asks, the facts, the consent answer and the endings; a rewording that drops a
number or identifier, or fails, is replaced by the scripted line, and turns are cached for regrading.

Opus 5.5 builder (job `2026-10-23__08-42-32`, Claude Code, high effort, subscription; $19.03 of the $30
cap: $11.24 for A0 in 129 turns, $7.79 for A1 in 68), graded like the oracle: Q00 0.9, Q01 0.1, Q10 0.9,
Q11 0.9, reward 1.0; drift 8 of 9 measurable R1 cases absorbed (a1-05 already passed at A0), no
regression. Its two failures are capability misses the oracle shows are solvable: a0-01 (A0 and A1)
opened a Billing Engineering case for a token gap the published pages explain, and a1-03 said the
retry date was unknown instead of working it out from the four past incident status cards (failure
mode #4). The leak audit found no expected value, team name or "dispute" in any customer message.

Previous graded build (on the previous scenario set, before the S0/S1 rewrite):
Claude Opus 5.5 (high) in Claude Code, support agent served by gpt-6-luna (high):
Q00 0.8, Q01 0.0, Q10 0.8, Q11 0.9, so reward 0.0. Its failures were cases opened
that policy says not to open. The current 20 cases have not been graded yet.

Local checks need no model credentials: `tests/redwood_devsupport` (pytest) and
`scripts/check_redwood.sh` (ruff, strict mypy, xenon). One run validates the
plumbing; it does not establish reliability. Keep Status: Draft until repeated
runs calibrate the gate.
