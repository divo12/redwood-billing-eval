# environment/ — the task container

Everything the builder's container is made of.

| Path | What it is |
|---|---|
| `Dockerfile`, `docker-compose.yaml`, `entrypoint.sh` | Build and start the container; install `redwood_kit` to `/opt/redwood_kit` and the R0 corpus to `/app/R`. |
| `redwood_kit/` | The runtime every support agent runs on (messages, tools, hooks, skills, subagents, sandbox, billing console). Read by the builder, used by the verifier. |
| `app/framework/` | Builder-facing docs: how to build an agent, the contract, examples, the offline-RAG guide, retrieval probes. |
| `r1/` | Everything R1-only that the builder may see at R1, installed at root-only `/opt/redwood-overlays/R1` with the R1 records (`R/`) and graded delta (`db_r1.json`). `advance.py` copies `r1/framework/` over `/app/framework` and writes the R1 `schema.json`; the practice service reads `r1/development_seed.json` once the release is R1. |
| `app/workspace/` | The starter agent the builder begins from (becomes `/app/workspace`). |
| `advance.py` | Run as root after `build_r0` is scored: copies the R1 overlay into `/app/R`, records the release, and seals `/tests` so the next session cannot read the verifier. Running it twice does nothing. |
| `probe_key/` | Private references for scoring retrieval probes; not visible to the builder. |

## app/ (copied to /app, visible to the builder)

`app/` has no README of its own on purpose: everything in it is copied into the
builder's container, and a new file there would change what the builder sees.

| Path | What it is |
|---|---|
| `app/framework/` | Builder docs: `agent_building.md` (which layer owns which kind of change), `agent_contract.md`, `agent_examples.py`, `offline-rag/` (selective-retrieval guide), `retrieval_probes.json`. Start at `framework/README.md`. |
| `r1/` | Everything R1-only that the builder may see at R1, installed at root-only `/opt/redwood-overlays/R1` with the R1 records (`R/`) and graded delta (`db_r1.json`). `advance.py` copies `r1/framework/` over `/app/framework` and writes the R1 `schema.json`; the practice service reads `r1/development_seed.json` once the release is R1. |
| `app/workspace/` | The starter agent (also the baseline at `/opt/redwood-baseline`). The builder edits it in place; the verifier freezes and runs it after each step. |

Starter workspace layers:

| File | Layer | Holds |
|---|---|---|
| `system_prompt.md` | identity | Who the agent is and its authority. |
| `AGENTS.md` | principles | How it retrieves, checks evidence, hands off. No exact billing numbers. |
| `tools.py` | actions | `@is_tool` methods: console queries/writes, search; code-enforced limits. |
| `hooks.py` | guardrails | `@hook` methods run around every message and tool call. |
| `skills/` | procedures | Procedures loaded on demand. |
| `subagents/` | specialists | Separate-context helpers. |
| `agent.py` | runtime glue | Loads the above and runs the model loop (the verifier's entry point). |

## redwood_kit/ — the support-agent runtime

Installed at `/opt/redwood_kit`. A workspace's `agent.py` imports it; the verifier
(`tests/run_agent.py`) uses it to run the agent in a sandbox.

| Module | Responsibility |
|---|---|
| `messages.py` | Typed conversation messages (`UserMessage`, `AssistantMessage`, tool messages) and their JSON encoding. |
| `toolkit.py` | `@is_tool` declarations: turns workspace methods into JSON tools and runs tool calls. |
| `hooks.py` | `@hook` declarations: guardrails that run around developer messages and tool calls (may block or rewrite). |
| `skills.py` | Loads `skills/<name>/SKILL.md` and exposes them as a load-skill tool. |
| `subagents.py` | Loads `subagents/<name>.md` and runs each specialist in its own context. |
| `agent_context.py` | The active workspace context and typed gateways (model, embeddings, billing) passed to agent code. |
| `candidate.py` | The sandboxed side of the wire protocol: reads `init`/`turn`/`tools` requests from the host, answers in JSON. |
| `embeddings.py` | Embedding models the runtime serves; calls go back to the host. |
| `retrieval_score.py` | Bounded local service that scores retrieval probes against private references. |
| `jsontypes.py` | `Json` / `JsonObject` aliases for data that crosses a JSON boundary. |
| `billing/` | The billing console, see below. |

Rule of thumb: the agent (inside the sandbox) only ever sends requests; the host
(`tests/run_agent.py`) owns the model keys and the billing store.

### redwood_kit/billing/ — the billing console

The agent's only write path, and part of what the grader checks. `client.py` runs in
the sandbox and only forwards JSON requests; `store.py` runs on the host and is the
source of truth. Read in this order:

1. `schema.py`: releases, tables, field descriptors, row accessors.
2. `validation.py`: JSON shape, references, and the rules a write to a writable table can break; `check_row` and `check_rules` check one row against the committed state.
3. `store.py`: the host console (money format, typed `ConsoleCall` log, replay, projection).
4. `client.py`: operations, error codes, and the typed client that sends them over the wire.
5. `development_seed.json`: synthetic R0 practice records. `store.py` also runs the root-owned practice service (`python -m redwood_kit.billing.store --serve`, socket `/run/redwood/practice-console.sock`) that `client.development_billing()` connects to: a fresh console per connection for `/etc/redwood-release`.

The builder reaches the console only through `client.py`; `schema.py`, `validation.py`, `store.py` and the seed are root-only.

Each seed holds only rows dated by its release's today (R0 2026-09-15, R1 2027-04-08). Only `credit_ledger` is R1-only. R1 requires
a priority when escalating, and shows money as `{value, currency}` instead of `_cents`.
Keep `describe()` output, error messages, money objects and cursors byte-identical:
builders' agents see them and the grader checks them.
