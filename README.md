# redwood-billing-eval

A two-step Harbor task: an agent builds a billing-support agent for Redwood Inference
from a company knowledge corpus (R0), then maintains it when the corpus grows (R1).

| Path | What it is |
|---|---|
| `tasks/redwood-devsupport/` | The Harbor task: `task.toml`, steps (`build_r0`, `maintain_r1`), environment, hidden verifier, oracle. Start with its `README.md` and `Task.md`. |
| `tasks/redwood-devsupport/environment/corpus/` | The corpus: `R0/` (start) and `R1/` (records added for `maintain_r1`). Derived from EnterpriseRAG-Bench (MIT) with task-authored policy records; see `NOTICE`. |
| `tests/redwood_devsupport/` | Unit tests for the verifier, billing console and runtime kit. |
| `scripts/check_redwood.sh` | Lint, strict types, complexity gate. |
| `skills/` | Authoring skills used to write the Redwood records. |

## Run

```sh
harbor run -p ./tasks/redwood-devsupport -a oracle -o "$PWD/jobs"                                    # oracle
harbor run -y -c tasks/redwood-devsupport/run-config.claude.json -o "$PWD/jobs"                     # Opus 5.5 high, Claude Code
harbor view ./jobs
```

Needs only `ANTHROPIC_API_KEY`; embeddings run on a local model. See `tasks/redwood-devsupport/README.md`.
