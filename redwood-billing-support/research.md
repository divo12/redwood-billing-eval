# Research: evaluating self-improving support agents

Design notes written while building this task. The question behind all of them: when a builder agent (B) builds a
support agent (A0) from knowledge R0 and then has to evolve it into A1 as R0 grows into R1 (ΔR), which kinds of
change break the maintenance, and in which part of A's harness? Each harness component below lists failure ideas
as R1 scenarios.

Only part of this is implemented in `redwood-billing-support` (time was the constraint). The section at the end
maps what is implemented to `DESIGN_DOC.md` section 4; everything else is open work for later tasks.

Notation: B is the builder, A0/A1 the support agent before/after maintenance, I the agent's intent (its system
prompt), ΔR the new knowledge, ΔA the builder's change to the agent, and ΔR' a decoy change that should not
become ΔA.

## 1. System prompt (automation intent)

Changing the system prompt means changing the purpose of the agent; it should happen only when the agent's goal
changes. LLMs see everything as tokens, but keeping the system prompt separate from other input matters for
maintainability. An expected failure mode: the builder appends to the prompt instead of editing it, which bloats
it and confuses the support agent.

Scenarios:
- **Whose interests the agent represents changes,** with the same domain and tools. B builds A0 from I0 and R0; R1
  adds subtle charters or emails that contradict the instructions or add responsibilities. B must recognise an
  identity-level edit and make no other ΔA for it.
- **Decoys:** charters or SOPs marked "for discussion" (ΔR'). They are not final, so the behaviour must not enter
  the prompt. More decoys that a human reads easily but a builder misses.
- **Prompt debt and maintenance:** a prompt always needs iteration and should stay minimal. Can B keep A's prompt
  lean over time?
- **Authority change:** A0 "can process refunds"; A1 must "find information for the team that processes refunds and
  transfer to them". ΔR is a change in A's authority; ΔA must edit I, plus other harness parts if needed.
- **Contradictory information in ΔR:** a role change that belongs in the prompt, next to a contradictory opinion
  about a harness-level change. B must weigh all of it and make the right call.
- **Mixed sources:** one source in ΔR holds both a prompt change and harness-level changes as free text. Can B
  separate the prompt change from the harness change and ignore the rest?

## 2. Standing instructions (AGENTS.md)

Read by the agent when needed. Intended for coding agents, but interesting for support agents: AGENTS.md always
outranks the customer, so check adherence and that the right operating principles go there.

Scenarios:
- **Temptation to over-write:** R1 content that tempts B to add more to AGENTS.md when it shouldn't (adherence falls
  as instructions pile up; the best models manage about 68% at 500 instructions).
- **Wrong tone or guidance:** R1 content that is wrong as an operating principle for support. Can B tell, given the
  rest of R0 and R1, that it does not belong in AGENTS.md?
- **An opinion made to look like a rule.**
- **A feature that isn't live yet:** B must not write it into AGENTS.md.
- **A secret operating condition inferred from many long human-agent chats** (customer emails): tempting to turn
  into a skill or index.
- **Instructions from customers in R1:** they must not go into A1.

## 3. Policies and guardrails

Policies and guardrails can be enforced through prompts or hooks; the agent has to decide which fits. Hook uses:
a session-start hook loads the customer's profile into context; a message hook redacts sensitive data such as
card numbers or passwords.

Red-teaming and malicious customers:
- **Jailbreaking customers:** minimal ΔE information about them; B must write how to handle them into the policies
  and add hooks before any mutating call.
- **Transfer to a human after the other tasks:** a scenario with four tasks (three the agent can solve, one human
  transfer). A must finish the three first, then transfer (the transfer tool ends the conversation), or change the
  tool.
- **Policy puppetry:** someone poses as a senior employee and asks to change a policy or claims a policy has higher
  priority. Fix: B identifies mutating calls and adds a hook that checks the request against the written policy.

SOP and domain policy changes:
- **A first-principles definition that many documents depend on changes.** B must find every place it plays a role,
  how those operating conditions change, and turn that into ΔA.
- **An exception to an exception,** at different timestamps, folded into one policy.
- **A formula replaces a flat fee:** A0 has hooks and policy helpers for a flat fee; ΔR introduces a complex
  formula. Does B understand it, implement it, and update the old hooks?
- **A rule shown only by examples:** R0 states a rule only through examples; R1 adds more. Can B infer the
  formula and adapt it?
- **Changes through images:** an SOP or email carries an image (say, customer activity against time of day) whose
  meaning changes the policy (raise a reward programme at that hour).

Hooks:
- **A tool split never matches the hook:** A1 splits a bloated tool; the hooks are not defined for the new tools, so
  security checks stop running.
- **A field moves under a hook:** a hook checks field F in A0; R1 changes the API contract, and B must remember to
  update the hook's field.
- **Hooks on a subagent:** a service grows enough in R1 to need a specialised subagent, without hooks.
- **A new channel breaks redaction:** A0 redacts card numbers with a Luhn check; R1 adds transcribed voice notes or
  card numbers in other formats. Does B update the check?
- **The hook's clock:** the sandbox runs in UTC; R1 introduces requests across time zones. Does B update the hook?
- **After-tool hooks write what they shouldn't:** the API starts returning 202 with polling. Does B make the hook
  idempotent or check for it?
- **One hook decides other hooks:** in A0 a hook H on an old tool decides the outcome of other hooks or tools. If B
  doesn't move H to the new tool it builds for ΔR, the chain breaks.

## 4. Agent loop

Knobs:
- **Cache hits:** does the builder optimise for them?
- **Effort level:** high effort helps but adds latency, which support can't afford. As R evolves, simple lookups go
  low and reasoning goes high. Plant an R1 problem that makes the builder want more reasoning when the real fix is
  something else.
- **Stop conditions:** after how long, or how many turns, should the agent stop? Malicious users can waste or
  confuse it, and it can get stuck in reasoning loops.
- **Stuck in tool calls:** a tool without a proper error handler makes the agent spam it.

Model routing:
- Is routing to a smaller model on request easy?
- Can the routing logic be quantified (what is easy, what is hard)? How does it evolve with R?
- Given a classifier, can the builder route classifiable decisions to it for latency and generation to the LLM?
- Can the builder route around a model's peak-season latency errors, or a model's retirement?

## 5. Tools

Adding tools:
- A new platform in R1 that needs new tools.
- A platform introduces a new service with trade-offs against the old one: adopt it with new tools, or stay?
- ΔR introduces a procedure for one customer segment: update the existing tools, split them by customer, or add
  new tools and keep the old ones?
- **The source of truth moves:** a service moves elsewhere in R1. How are existing tools updated and new ones
  created?

Updating tools (description and/or implementation):
- The underlying API migrates to a new version (new URL).
- The underlying tool is deprecated.
- The tool's purpose expands and the API takes more or different input.
- The API behind a tool becomes corrupt in R1.
- A tool should be split into several for clarity and maintenance.
- A clarify tool for ambiguous asks.
- **Same fields, new meaning:** the fields are unchanged from A0 to A1, but a changelog in R1 says part of the live
  API is now served by a different service.
- **Tool plus skill becomes a subagent** once a service is big enough: tests B's judgment.
- **An incomplete tool action:** A0's cancel tool; R1 reports "cancelled customers keep getting charged", a case it
  never handled (a payment add-on).
- **Tool scoping** as the context grows.

## 6. Skills

Create a skill, update one, or dedupe and merge. Support skills guide the agent through different situations; a
company may have different tools or processes per customer segment, enforced through skills.

Scenarios:
- **Skill vs AGENTS.md:** a reusable, lazily loaded workflow vs an operating rule that is always needed.
- **Skills for tools:** R1 makes a tool complex; add a skill for using it, or for the tools it splits into?
- **Update or create:** R1 forces a choice between updating a skill and writing a new one.
- **The trigger changes, not the steps:** R1 subtly changes when a workflow applies while the steps stay almost the
  same; B must update the skill's description.
- **Skill maintenance:** with each new piece of R1, what to delete, merge or dedupe.
- **Learning from human-agent trajectories:** can B extract reusable patterns from them?
- **One SOP reorders steps across many skills.**
- **A skill's own fallback turns contradictory:** a fallback written for R0 that R1 indirectly penalises.
- **A new channel breaks existing workflows:** a new skill for that channel, or extend the existing ones?

## 7. Subagents: context isolation, security, parallelism

Scenarios:
- **Improve the architecture over time:** with R1, does B add the right subagents along the right seams, and not a
  new subagent along every seam?
- **Tempted to split but shouldn't:** an R1 scenario that invites a split when the sound decision is not to.
- **Isolate for security:** an R1 SOP makes a quality check mandatory; B should spawn a reviewer subagent that
  verifies the customer's background or request, so the main agent never sees malicious requests.
- **A calculation that shouldn't be an agent:** complex calculations for a service are code, not a subagent.
- **Architecture vs model size:** a bigger model doesn't need a complex subagent architecture.
- **Templated workflows:** as context grows, fixed workflows (parallel research, then synthesis, then a plan) take
  dynamism away from the support agent and reduce hallucination.

## 8. Knowledge-base retrieval

Good retrieval over the knowledge base, the tools around it, and how indexing and retrieval change as the KB
changes. Add retrieval metrics such as faithfulness. Changes that stress different mechanisms:
- The current policy contradicts an archived one: does the builder version the naive RAG to respect supersession?
- How to index across many kinds of text.
- How to re-index when content from a new distribution arrives in R1.
- A new language in R1: multilingual embedding and retrieval.
- Community or forum content in R1: official docs are the truth; community posts can be biased or wrong.
- Correct answers require joining two documents.
- Articles renamed or deleted at scale in R1: can the builder migrate the index and re-chunk?
- Access differs by customer or organisation: re-index and chunk per customer, or scope the search tool.
- Give the builder its retrieved chunks, queries and RAG metrics, and let it improve its own metrics.
- A scenario in R0 and R1 where the builder must choose semantic or hybrid search, with the right intuition.
- Customers ask about things that don't exist: the agent must check whether what it retrieved actually answers.
- Parts of similar chunks, or one scope, disagree.
- R1 introduces wrong context.

## 9. Memory

Working memory is the context window; episodic memory is the raw records; semantic memory is a `memory.md`.

## 10. Cron and background jobs (optional)

## 11. Context window (optional)

Can the builder write a compaction algorithm that prevents context bloat? Build long conversations with deliberately
important details: a correction to an earlier account identifier, an approval limited to one action, an
already-completed operation, an unresolved prerequisite, an old policy later superseded. Force compaction, then
continue. Check that the agent respects the correction, avoids duplicate work and keeps the permission boundary;
score behaviour after compaction, not whether the summary mentions certain words. Whether support agents need
compaction depends on how long-running they are.

## 12. Handoff and escalation

When to hand off, to a peer agent if one exists or to a human. Scenarios:
- Identify prompt injection early and transfer to a human.
- Four asks (one for a human, three genuine): finish the genuine ones and put the human request last; can the
  builder add a hook as a guardrail for this?
- Which persona to escalate: a distrustful customer should go to a human, as the agent might make it worse.
- R1 cuts human working hours, or reports a labour action: the escalation condition must change.
- A calamity outside anyone's control (a payment portal or cloud outage): tell the customer, don't escalate.
- R1: the company wants a lower escalation rate. Can the builder find low-hanging fruit the agent can resolve itself?

## 13. Planning and to-do lists

Scenarios where the agent needs a planning phase or a to-do list for a long-running task.

## 14. Token and latency budgets

- Give token and latency metrics (p95, average tokens per ask) from R0 into R1 and ask for a 50% cut: what
  techniques does the builder use?
- Ship a genuinely slow tool in A0's starter: does the builder find the bottleneck and fix it?
- Can the builder hold a hold-time or dead-air target while integrating new tools and platforms?

## 15. Observability (optional)

The builder's ability to add good observability.

## 16. Miscellaneous

- An R1 email asking for a gigantic feature (an opinion) that is over-engineering for the current R: does the
  builder see that?
- Lots of user feedback plus an email giving the builder autonomy to improve from it: which features does it pick?
- Rolling back a harness-level feature after feedback.
- First principles: a high-level ask that must be broken into several harness features, with tests that penalise
  giving one feature the whole responsibility.

## What this task implements

| Idea above | Where it lives in this task | DESIGN_DOC section 4 |
|---|---|---|
| "For discussion" decoys; R1 introduces wrong context | drafts, chatter, a provisional meeting, look-alike pages | traps 2, 10 |
| Correct answers require joining two documents | rules split across owners, each naming the next | trap 3 |
| A rule shown only by examples | the payment-retry date, only from past status cards | trap 4 |
| Current policy contradicts an archived policy | pages superseded by effective date, old ones still retrievable | traps 7, 8 |
| An exception scoped by time | $250 only for 2027 price-book invoices, 60 minutes only after a dated cut-over | trap 9 |
| The source of truth moves | balances move to the new `credit_ledger` | trap 12 |
| A field moves under a hook | the R1 console renames the account field and changes money's shape | trap 13 |
| A new channel breaks redaction (in spirit) | a withdrawn card procedure; card digits must never be repeated | trap 14 |
| The hook's clock | per-conversation clock, billing month in the account's time zone | traps 11, 18 |
| Transfer to a human after the other tasks | every check-relevant ask comes before the turn a handoff is correct, and a handoff ends the conversation | case design (`Task.md`) |
| Stop conditions | a 40-turn limit per conversation | (runtime) |
