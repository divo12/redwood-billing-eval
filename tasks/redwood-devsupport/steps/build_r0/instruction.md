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
table, and search with a vector, with text, or with both. The support
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

`framework/offline-rag/README.md` includes practice questions and a score
service for when you want to check retrieval quality while you change how
the agent finds evidence. Test the behavior of the agent you actually ship.
Use `redwood_kit.billing.development_billing()` to test console-facing
tools and hooks against release-matched synthetic accounts. The cases are
listed in `/app/framework/client_api/practice_cases.json`; writes stay local
and are not graded.

Success is measured on held-out billing-support conversations: correct,
current answers, appropriate clarification, and correct handoffs.
