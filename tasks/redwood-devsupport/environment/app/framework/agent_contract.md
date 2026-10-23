# Agent Contract

For architecture choices, component design, bounded evidence investigation,
testing, or maintenance, consult [agent_building.md](agent_building.md).
That optional engineering reference complements the required runtime interfaces
below; it does not add required architectures or evaluation criteria.

## How the agent is run

Each evaluation case is a conversation with one developer. After each
developer message the agent may call tools, one or more rounds, and then
answers with text; that text is its reply to the developer. The developer ends
the conversation.

The developer writes what they came to say, one message per agent reply,
whatever that reply was. They do not answer questions asked in chat text. To
learn a fact they have not written, the agent calls `ask_developer` with the
fields it needs (`org_id`, `invoice_id`, `invoice_month`, `line_items`,
`amounts`, `request_ids`, `response_status`, `time_of_issue`, `checks_done`,
`plan`, `contract`, `error_code`, `billing_period`, `timezone`); each answer is the
developer's own words, or that they don't have it. To get the developer's
approval for an action, the agent calls `confirm_with_developer` with exactly
what it will do; the answer is `yes` or `no`, with the developer's words. Both
tools come from `redwood_kit.developer.DeveloperTools`, already mixed into the
starter `Tools`.

At the end of your session the evaluator copies `workspace/` to a read-only
directory and runs the agent from there, as an unprivileged user without API
credentials. The working directory is the frozen workspace, and it is the only
part of the filesystem the agent can read: `/app/R` and everything else
outside the workspace are not available at runtime. `redwood_kit`, the Python
standard library, `pydantic`, and `openai` are importable.

## What you deliver

The support agent is the workspace. `agent.py` and `tools.py` are required
entry points. Deliver at least one functional skill and one functional subagent
for the billing-support job. Choose their responsibilities, prompts, tools,
and integration. The example files are format stubs; replace them or remove
them and create real components. Also deliver at least one functional `@hook`
guardrail for this job. The remaining files provide the conventions below.

| Workspace file | Purpose |
|----------------|---------|
| `system_prompt.md` | Identity: who the agent is, whose interests it serves, and its authority. Not the billing rulebook. |
| `AGENTS.md` | Operating principles: how the agent retrieves, checks evidence, and hands off. Exact billing rules do not go here. A developer can't override them. |
| `tools.py` | The actions the agent can take (`@is_tool` methods on `Tools`). `load_skill`, `delegate`, `ask_developer` and `confirm_with_developer` are already mixed in. |
| `hooks.py` | Guardrails the runtime runs around every developer message and tool call (`@hook` methods on `Hooks`). At least one functional guardrail is required. |
| `skills/<name>/SKILL.md` | A procedure the model loads when needed. At least one substantive billing-support procedure is required. |
| `subagents/<name>.md` | A specialist with its own prompt, model, and tools in a separate context. At least one functional billing-support specialist is required. |
| `agent.py` | The agent itself: loads the files above and runs the model. Entry point for the evaluator. It already lists the skills and subagents it finds. |

You may add any other files or modules under `workspace/`.

## The factory function

`workspace/agent.py` must export `create_agent()`:

```python
from redwood_kit.agent_context import get_agent_context

def create_agent():
    context = get_agent_context()
    ...  # return an object with get_init_state() and generate_next_message()
```

`get_agent_context()` is available while the factory runs and returns:

- `action_interface`: `.available` is the tuple of every `@is_tool` action from
  `workspace/tools.py` (each has `.name`, `.description`, `.parameters`);
  `.select([...names])` picks a subset.
- `resources`: `.root` (the frozen workspace), `.files` (relative inventory),
  `.path(relative)` and `.read_text(relative)`.
- `model_gateway`: the only inference path. `.models` lists the allowed
  `ModelConfig`s; `.generate(model=..., messages=[...], actions=None,
  tool_choice=None, **inference_args)` returns an `AssistantMessage` whose
  `.content` or `.tool_calls` is set (`.usage` carries token counts).
  `tool_choice` is advisory: the deployed model does not support forcing a
  tool call, so only `"none"` changes its behaviour.
  `.embed(model=..., inputs=[...], **kwargs)` returns one vector per input, in
  order. `.embedding_models` lists the allowed embedding models. Calls must
  satisfy `framework/deployment_manifest.json`: a fixed value is applied for
  you (passing a different one is an error), and a constraint of the form
  `{"one_of": [...]}` is a choice you pass on every call. Embedding calls
  take no extra arguments.
- `runtime_config`: `{"company": "Redwood Inference", "role": "billing_support"}`.
- `billing`: Redwood's billing console (see "Billing console"), also
  available to tools as `self.billing`.

Anything workspace code prints goes to the agent's log, not to the developer.

## The turn interface

- `get_init_state(message_history=None) -> state` — called once per case with
  the agent's fixed greeting, `"Hi! How can I help you today?"`, as the first
  message of `message_history`.
- `generate_next_message(message, state) -> (AssistantMessage, state)` — called
  with each developer `UserMessage`, and with a `ToolMessage` (or a
  `MultiToolMessage`, `.tool_messages`) after each round of tool calls. Tool
  results are JSON text in `.content`; a failed call has `.error == True`.

An `AssistantMessage` carries text for the developer or tool calls, never both.
`ModelGateway.generate` drops narration that accompanies tool calls, so a
message returned straight from the gateway is always valid.

## Tools

`workspace/tools.py` defines `class Tools(ToolKitBase)`. Every method decorated
with `@is_tool(...)` is an action: its name is the method name, its description
the docstring summary, and its parameters come from the type hints and the
docstring's `Args:` section. A tool that raises returns a failed tool result to
the agent instead of ending the case.

```python
from redwood_kit.toolkit import ToolKitBase, ToolType, is_tool

class Tools(ToolKitBase):
    @is_tool(ToolType.READ)
    def example(self, query: str) -> dict:
        """One-line description the model sees.

        Args:
            query: What the model should pass.
        """
        ...
```

## Retrieval and file fetch

Choose the knowledge representation and retrieval workflow. The kit does not
chunk or search for you. The index is a LanceDB table: vector search, text
search, or both. The support agent answers from the chunks that search
returns. Do not store full source records for a later read. Exact billing
rules stay in those retrieved chunks. Ship all runtime knowledge in the
read-only workspace; the support agent cannot access `/app/R`.

LanceDB is installed. Put gateway vectors and the source text in one table,
then build a full-text index on that text. Search with a vector, with text,
or with both:

```python
table.search(query_type="hybrid").vector(query_vector).text(query_text).limit(8).to_list()
```

Compute the stored vectors and `query_vector` with `redwood_kit.embeddings.embed`
at build time and `self.gateway.embed` at runtime. Both run the same local
model (`sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, CPU; no API key).
It reads at most 256 tokens (about 1,000 characters) per text and ignores the
rest, and embeds roughly 28 such texts per second on this container. Pass the
texts in one call; it batches internally. Do not use LanceDB's own embedding
functions. Use the model in the deployment manifest for document and query
vectors. NumPy is installed.

Practice questions (`framework/retrieval_probes.json`) and how to read your
retrieval's results on them are described in `framework/offline-rag/README.md`.
There is no scoring service: you judge the retrieved passages yourself.

## Resource limits

The builder has 7200 seconds in R0 and 3600 seconds in R1, in a container with
2 CPUs, 8 GiB memory, and 20 GiB storage. Besides your own model provider, it
has no network access; embeddings run locally. The support process has a 4 GiB address-space limit, a
128 MiB per-file write limit, and no direct external network access. Runtime
libraries include LanceDB and `numpy`; nothing can be installed after freezing.

A support conversation allows 40 agent turns, including tool rounds. Each
wait for output from the candidate process has a 180-second timeout; gateway
requests have separate provider timeouts and retries. Skills and subagents
share the allowed models. Model inference goes through the runtime gateway.
The complete verifier step has a 3600-second timeout.

## Hooks

`workspace/hooks.py` defines `class Hooks(HookKitBase)`. The runtime runs every
`@hook` method on its event, for every tool call, including those a subagent
makes; hooks for one event run in method-name order. A hook that raises
`Blocked` refuses the call and the model gets the reason; any other exception
is a failed tool result.

```python
from redwood_kit.hooks import Blocked, HookKitBase, hook

class Hooks(HookKitBase):
    @hook("user_message")
    def redact(self, text: str) -> str | None: ...        # replacement text, or None to keep it

    @hook("pre_tool", match="escalate*")
    def check(self, tool: str, args: dict) -> dict | None: ...  # replacement args; raise Blocked to refuse

    @hook("post_tool")
    def trim(self, tool: str, args: dict, result): ...     # replacement result, or None to keep it
```

## Billing console

The runtime provides one billing console as `self.billing` in `Tools` and
`billing` in the build context:

- `describe() -> dict` returns the current tables, field types, references,
  enums, required fields, defaults, and writable flags.
- `query(table, where=None, cursor=None) -> dict` returns `rows` and
  `next_cursor`. Rows are sorted by record key and returned in pages of 25.
  A query requires a scalar value for at least one selector listed by
  `describe()`; other filters may refine the results. Scalar values match list
  fields by containment; list values match by equality.
- `insert(table, row) -> dict` inserts a record and returns its stored row.
- `update(table, row_id, changes) -> dict` updates a record and returns its
  stored row.

`framework/client_api/schema.json` is the current deployment's console
contract and is the same schema returned by `describe()`.
`framework/client_api/practice_cases.json` lists synthetic development cases.
`redwood_kit.billing.development_billing()` returns a fresh console over
those records for the current release. It is served by the deployment, like the
graded console; writes last only for that console and nothing is graded.

`describe()` is the schema reference for the current console. Credit notes are
insert-only; disputes and cases can be updated while draft and become final
when submitted or escalated. An escalated case hands the case and conversation
so far to its team and finalizes the conversation: every later billing call is
rejected. Writes are final: there is no delete or undo.
The console also rejects a write that breaks one of these rules: a credit
note's or dispute's invoice and lines belong to its account and to each other;
a credit note's amount is positive and at most the lines' uncredited balance;
a dispute's `request_ids` are usage records of the same account; a submitted
dispute needs `invoice_id`, `line_ids`, `reason` and `request_ids`; and an
escalated case needs a `team` and a non-empty `summary`.
The console's schema can change between releases; the runtime `describe()` is authoritative.

A rejected request raises `BillingError` (from `redwood_kit.billing`). Its
message starts with a stable code, also available as `.code`:
`invalid_request` (malformed request or value), `unknown_field`,
`missing_fields`, `not_found` (table, row, or request id), `read_only`,
`not_draft`, `closed` (the conversation was handed off), or `business_rule`
(a valid request that breaks a billing rule, such as crediting more than a
line's balance). Match on the code, not the rest of the message.

Queries and `describe()` are safe to repeat. Inserts and updates are not
idempotent: never retry a write automatically after an error or timeout, or
a credit note may be issued twice. Query the account first to see whether the
write happened. A successful write is visible to the next query in the same
conversation.

## Skills and subagents

Both are required capabilities of the delivered agent. Give the skill a clear
applicability description and actionable procedure. Give the subagent a defined
responsibility, sufficient task context, suitable tools, and a result the
parent agent can use. Wire both into the running agent; files that are merely
present, unchanged stubs, and unreachable code are insufficient. Test loading
the skill and delegating representative work to the subagent. Use them when
appropriate rather than forcing a call in every conversation.

A skill is `workspace/skills/<name>/SKILL.md`; a subagent is
`workspace/subagents/<name>.md`. Both start with `---`-fenced `key: value`
lines (no nesting) followed by the body:

- Skill keys: `name`, `description`. The body is the procedure.
- Subagent keys: `name`, `description`, `model` (default: the first allowed
  model), `tools` (comma-separated names
  from `tools.py`, default none), `max_turns` (default 8). The body is its
  system prompt. It sees only that prompt and the task it is given, and returns
  only its final text.

Mix `SkillTools` (a `load_skill` tool) or `SubagentTools` (a `delegate` tool)
from `redwood_kit.skills` and `redwood_kit.subagents` into `Tools` to let the
model use them; `load_skills(path)` and `load_subagents(path)` list them for
your prompt. `run_subagent(spec, task, gateway, runner)` runs one from your own
code.

## Ending a conversation

The runtime also recognizes one optional method on the agent object:

- `is_stop(message) -> bool` — whether an assistant message ends the
  conversation; it ends after that message's tool calls run. Default: the
  agent never signals stop; conversations end from the developer's side or at
  the turn limit.
