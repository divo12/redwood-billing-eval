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
R0 contains 3514 source records; R1 adds 3522 without replacing R0 paths.
The build begins with R0 at `/app/R`. After the first evaluation, the existing
root-only transition reveals R1 in the same container. The builder continues
its same builder session and updates its existing workspace. R2 remains absent.
R1 is the enterprise evolution ΔR. It adds the R1-only `credit_ledger` table
(`data/db_r1.json`, merged over `data/db.json` only in R1) and ten planted
records: six rules (support charter 2027, credit ledger, payment-card data,
payment failures during processor incidents with its status page, record format
changes, confirm-before-write quality standards) and four decoys (cache hits
half price, skip confirm for small credits, a draft refund-authority proposal,
one subagent per product). The R0 credits page's R1 v2 lowers the
duplicate-credit limit from $500 to $250 per line. Every one of the 20
`maintain_r1` scenarios exercises a ΔR change; unchanged behavior is measured
by replaying the 10 R0 scenarios on R1 (Q10).

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
conversation reward (`reward`) does not score artifact presence. Since 1.0.0,
`reward.json` also reports `artifact_quality`: code checks for the instruction's
size limits, a non-stub skill, subagent and `@hook`, and whether some graded
conversation loaded the skill and delegated to the subagent. It does not change
`reward`, and component quality still needs trace review.

Each hidden case in `tests/data/billing_scenarios.json` is a simulated
conversation (10 per step, 20 in total), chosen so that every check is decided
by code: build_r0 keeps a0-02, a0-04, a0-05, a0-10, a0-12, a0-18, a0-22, a0-24,
a0-25, a0-31; maintain_r1 keeps a1-05 to a1-10, a1-15 to a1-18. Cases whose
correctness lived only in explanation wording were dropped. The runtime provides one typed record store with
`describe`, `query`, `insert`, and `update`; its schema is discoverable through
`describe()`. R1 exposes cents as money objects across console operations and
requires a case priority when escalating. The shared baseline is `data/db.json` (plus `data/db_r1.json` in R1),
copied into the image at root-only `/opt/redwood-data/db.json`; each conversation
gets its release view.
Gold and alternatives are generic insert/update traces replayed from that
baseline. Grading compares projected writable tables, ignoring generated
identifiers and leftover drafts. The action check (hyper-tau's ACTION on
console operations) requires each listed `query:`, `insert:`, `update:`, or
`write:` operation to match a successful call: the account lookup, handoff
facts in the case or the conversation before it, and text no write may store.
The case reward and Harbor's averaging remain unchanged; practice retrieval
metrics are not part of reward.
- **COMMUNICATE.** Values the agent must tell the developer, derived from the
  console or R (e.g. a1-09's ledger balance and both grant expiry dates); any
  listed form counts, and no value appears in the developer's brief.
- **NEVER_SAY.** Values no agent message may contain (a1-16/a1-17 card digits).
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
  scenarios' invoice, request or subscription IDs anywhere in R0, R1 or
  `environment/app`. R1 answers rely on R1-only records (the hosted-API
  packages policy, the discounting-and-credits policy, the marketplace
  invoicing notes, and the 2027 support records) or R1 console data.
- Scenario DB checks accept every outcome the cited records allow: for
  example a1-10 (expired promotional credit) accepts a finance handoff or no
  case, as long as nothing is promised.
- Placement (axis 1), maintenance health (axis 2), and ΔR uptake/regression
  (axis 3) are diagnostics in `tests/evolve/diagnostics.py`;
  they never change reward.
- Cost follows hyper-tau's credit requirement: agent tokens at the published
  rates, set-wide mean against a published budget, soft overage subtracted from
  the score. The budget is `null` (no penalty) until an oracle run calibrates it.
- Fairness is not yet calibrated: no Q00/Q01/Q10/Q11 run has completed (the
  available OpenAI key allows 50 requests/day per model), so false rejections,
  shortcuts, and A0-fails/A1-passes ordering are unmeasured until a complete
  run is read.
- References, future overlays, baseline, verifier files, and score logs are
  root-only. The practice socket is root:harbor 0660 and unreachable by the
  support user. Its directory is traversable so builder access works even
  when startup uses a restrictive umask. Safe logs contain hashes and timing,
  not credentials, passages, or hidden answers.
- `Task.md`, the research report, `tests/`, and `solution/` are not copied into
  `/app`. `environment/probe_key` is installed only under the root-only path.
- This is a frozen simulated company and support environment, with predefined
  allowed models and no direct production services. It does not measure live
  enterprise connector behavior, unlimited infrastructure choice, or discovery
  across unseen companies.

## Oracle results and triage

Historical: these results predate the judge removal and the 10+10 case cut, and need re-running on the current verifier. Reference oracle, rebuilt indexes, judge at low reasoning. Runs 2 and 3 are after
the deterministic-routing edits (run 3 also has the oracle and grader fixes below).

| Run | Q00 | Q01 | Q10 | Q11 | Drift (Q11-Q01) | Regression (Q10-Q00) |
|---|---|---|---|---|---|---|
| 2 | 0.90 | 0.10 | 0.95 | 0.70 | +0.60 | +0.05 |
| 3 | 0.90 | 0.10 | 0.95 | 0.85 | +0.75 | +0.05 |
| pass^2 (runs 2+3) | 0.80 | 0.05 | 0.90 | 0.60 | | |

Final two full runs (B, C) on the current revision. pass^2 = passes in both runs; pass@2 = passes in at least one.
Q01 uses runs A and B (A0 and its R1 cases were unchanged). Q11 run B uses the a1-03 rerun after the console fix below.

| Metric | Q00 | Q01 | Q10 | Q11 | Drift | Regression |
|---|---|---|---|---|---|---|
| pass^1 (mean) | 1.00 | 0.125 | 1.00 | 0.95 | +0.825 | 0.00 |
| pass^2 | 1.00 | 0.10 | 1.00 | 0.95 | +0.85 | 0.00 |
| pass@2 | 1.00 | 0.15 | 1.00 | 0.95 | +0.80 | 0.00 |

Only remaining failure: a1-19 in both runs. The oracle explains the Free 402 but not the remedy (add a payment method or upgrade), which the R1 Free-tier policy states: a fair oracle miss.

After the five fixes in the table, the five affected cases (a0-25, a0-05, a1-03, a1-04, a1-07) pass 2/2 reruns.

Broken A1 (R1 oracle keeping the R0 $500 duplicate limit) on a1-05, a1-06, a1-19:
1/3 pass; a1-05 and a1-19 fail on the missing dispute, so the checks tell good
maintenance from stale maintenance.

Judge stability: the run-1 transcripts were rejudged at low and high reasoning,
with 1 flip in 228 answers (0.4%).

| Case | Call | Basis |
|---|---|---|
| a0-18 | R ambiguity, fixed in R | Routing page: an unconfirmed contract claim goes to the account team, including an SLA credit |
| a1-02 | R ambiguity, fixed in R | Routing page: confirming a contract figure needs no case |
| a1-03 | R ambiguity, fixed in R | Routing page: an older conflicting page gets an explanation, not a case |
| a1-13 | R gap, fixed in R; grader widened | Marketplace notes: a branded invoice needs Legal approval Redwood doesn't give; the judge accepts either phrasing |
| a1-11 | Grader widened | Credit ledger page: expiry is quoted from the ledger, so the judge accepts the ledger date or 12 months |
| a1-20 | Scenario brief | The simulator pushed for a review the brief didn't ask for; the brief now accepts the console amounts |
| a0-05, a0-22 | Oracle | Skill: route by what is asked; open the case before the details arrive (routing page) |
| a1-07 | Oracle | R1 duplicates page: the copy is the line no usage record points to; the tool returns `copy_line_ids` |
| a1-09 | Oracle | AGENTS.md: no figures from superseded guidance |
| a1-15 | Oracle | Skill: read the marketplace notes for marketplace-billed accounts |
| a1-04 | Grader too strict | Hosted packages policy: a paid plan with overage disabled returns 402, and the console can't show the setting, so a conditional caveat no longer fails |
| a0-25 | Grader too strict | The R0 console shows request IDs on invoice lines, so looking them up counts as well as asking |
| a0-05 | Judge wording | R0 says Redwood uses the model-native tokenizer; "its own tokenizer" also counts |
| a1-07 | Scenario brief | The customer refused before being asked, so confirmation was never needed; they now raise approvals only when asked to confirm |
| a1-03 | Scenario brief | The simulator asked for an escalation the brief didn't contain; the quota is now stated as used up |
| a0-04, a0-13, a0-22 | Oracle | It invited a dispute after explaining (routing page: explain and stop); it missed the 30-minute follow-up (routing table) |
| a0-22, a0-24, a0-25 | Grader too strict | Routing page lists what Billing Ops needs, not that Support must ask; the console shows invoices, lines and request IDs. Intake checks accept asking or looking up; ACTION `contains` and a0-22's "what to send" check still verify the facts |
| a0-05, a0-18, a0-12 | Oracle (A1 regression) | AGENTS.md: route by what is asked; open the case when saying a team must decide; handoff timing comes from Support routing records, not engineering SLOs (R0 routing page, still current in R1) |
| a1-03 | Environment data | The brief says current-period usage is used up, but the R1 console had none after the Apr 5 renewal; added two mossgate-dev usage rows (Apr 5-7) over the Free limit |
| a1-20 | Weak drift case | A0 passes it in both Q01 runs, so it adds nothing to drift |

## Validation and remaining work

The research behind the changes is in [docs/rag_research.md](../../docs/rag_research.md); that
document describes the pre-repair findings, not the current runtime contract.

Local verification: all tests in `tests/redwood_devsupport` pass, and
`scripts/check_redwood.sh` (ruff, strict mypy, xenon, no getattr/setattr)
passes. Command (no model credentials or paid requests):

```sh
uv run --python 3.12 --no-project --with pytest --with httpx --with 'pydantic>=2.9,<3' \
  --with 'openai>=1.100,<3' --with 'anthropic>=0.60,<1' --with 'fastembed==0.9.0' \
  --with 'lancedb>=0.22,<0.30' --with 'numpy>=2,<3' pytest -q tests/redwood_devsupport
```

Checks cover the billing console, the scripted developer and consent order,
the provider gateways (provider errors invalidate a run, bad agent requests do
not), grading, and the oracle workspaces. A scripted support conversation verifies that each
tool result reaches the next model call and appears in retained evidence.

Docker packaging checks built the actual image, exercised builder/socket
access in R0, denied access to references and future state, revealed the R1
overlay, checked cumulative record count and new-question availability without
restarting the scorer, and denied the support user access to R, references,
logs, and the practice socket. These are deterministic packaging checks, not
scored model runs. Harbor 0.22.0 resolves the current run config successfully.
The preceding v0.2 Harbor installation-only job `jobs/rag-contract-install-20260927` completed
without an exception. It built the environment and installed Pi using a
clearly fake placeholder for the required environment variable; its result has
no agent execution or verifier result. Its displayed zero is not an eval score.

For v0.3, all 49 existing tests pass. Offline pack 1.0.0's synthetic self-check
passes locally and inside the rebuilt image as `harbor`, with no API calls.
Its read-only inspector processed all 3513 R0 records without malformed JSON.
Packaging checks confirmed all three pack files are readable but not writable
by the builder, and hidden references remain inaccessible. Local documentation
links, version labels, and exact instruction/spec agreement were also checked.

The 1.0.0 runs (oracle, and Claude Opus 5.5 high in Claude Code) and their
analysis are in `RUN_REPORT.md`:

```sh
harbor run -p ./tasks/redwood-devsupport -a oracle -o "$PWD/jobs"
harbor run -y -c tasks/redwood-devsupport/run-config.claude.json -o "$PWD/jobs"
```

Read the complete builder trajectory, runtime model/tool contexts, scenario
evidence, and release transition before interpreting that score. One run can
validate plumbing, not establish reliability or calibrated practice cutoffs.
Keep Status: Draft until concrete spec review and actual calibration evidence
justify a stronger status.
