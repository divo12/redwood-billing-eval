# Agent Framework Reference

The support agent lives in `/app/workspace/`. The evaluator runs it with the
`redwood_kit` runtime (`/opt/redwood_kit`, importable from any Python process;
read it if a detail is unclear). The billing console runs as a deployment
service: `client_api/schema.json`, `describe()` and `agent_contract.md` are its
contract.

| Workspace file | Purpose |
|----------------|---------|
| `system_prompt.md` | Identity: who the agent is, whose interests it serves, and its authority. Not the billing rulebook. |
| `AGENTS.md` | Operating principles: how the agent retrieves, checks evidence, and hands off. Exact billing rules do not go here. A developer can't override them. |
| `tools.py` | The actions the agent can take (`@is_tool` methods on `Tools`), including `ask_developer` and `confirm_with_developer` for talking to the developer. |
| `hooks.py` | Guardrails the runtime runs around every developer message and tool call (`@hook` methods on `Hooks`). At least one functional guardrail is required. |
| `skills/<name>/SKILL.md` | A procedure the model loads when needed. At least one substantive billing-support procedure is required. |
| `subagents/<name>.md` | A specialist with its own prompt, model, and tools in a separate context. At least one functional billing-support specialist is required. |
| `agent.py` | The agent itself: loads the files above and runs the model. Entry point for the evaluator. |

You may add any other files or modules under `workspace/`.

| Framework file | What it covers |
|----------------|----------------|
| `agent_contract.md` | The `create_agent` factory, the turn interface, tools, embeddings, and the runtime sandbox |
| `agent_building.md` | Offline agent engineering reference 1.0.0: architecture, instructions, state, tools, skills, delegation, guardrails, evidence investigation, testing, and maintenance |
| `agent_examples.py` | Synthetic executable kit self-check: validated tools, a hook, skill loading, and a scripted delegate; no API calls |
| `offline-rag/README.md` | Versioned offline engineering pack: architectures, inspection, chunking, retrieval, working examples, and testing guidance |
| `deployment_manifest.json` | Allowed agent models, embedding models, and their inference constraints |
| `retrieval_probes.json` | Practice questions grouped by domain and applicable release; run them through your retrieval and read the results (no scorer) |

The deployment generates `client_api/schema.json` for the current console and
`client_api/practice_cases.json` for its synthetic development cases.
`redwood_kit.billing.development_billing()` provides a release-matched
synthetic console for local testing; writes stay local and are not graded.
`client_api/practice_conversations.json` holds example developer scripts for
`python -m redwood_kit.practice`, which runs whole conversations through the
graded conversation loop with the deployed support model (see
`agent_contract.md`, Practice conversations).
