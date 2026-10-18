# redwood-billing-eval

A two-step Harbor task: an agent builds a billing-support agent for Redwood Inference
from a company knowledge corpus (R0), then maintains it when the corpus grows (R1).

| Path | What it is |
|---|---|
| `tasks/redwood-devsupport/` | The Harbor task: `task.toml`, steps (`build_r0`, `maintain_r1`), environment, hidden verifier, oracle. Start with its `README.md` and `Task.md`. |
| `environments/redwood-mini/` | The corpus: `R0/` (start) and `R1/` (records added for `maintain_r1`). Derived from EnterpriseRAG-Bench (MIT); see `NOTICE`. |
| `tests/redwood_devsupport/` | Unit tests for the verifier, billing console and runtime kit. |
| `scripts/check_redwood.sh` | Lint, strict types, complexity gate. |
| `skills/` | Authoring skills used to write the Redwood records. |

## Run

```sh
harbor run -y -c tasks/redwood-devsupport/run-config.oracle.json -o "$PWD/jobs"   # oracle
harbor run -y -c tasks/redwood-devsupport/run-config.json -o "$PWD/jobs"          # agent
```

The verifier needs `OPENAI_API_KEY` (support agent runtime).
