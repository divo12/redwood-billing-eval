# steps/ — the two task steps

Each step has the same shape:

| Path | Purpose |
|---|---|
| `instruction.md` | What the builder is asked to do in this step. |
| `tests/test.sh` | Harbor's test entry: runs `/tests/verify.py <step>`. |
| `solution/solve.sh` | The oracle: installs a reference workspace instead of a real builder. |
| `solution/workspace/` | The reference agent the oracle installs. |

| Step | Instruction in one line | test.sh also… |
|---|---|---|
| `build_r0/` | Build the billing agent from `/app/R` (R0). | runs `advance.py` afterwards to reveal R1 and seal `/tests`. |
| `maintain_r1/` | Continue the session; `/app/R` has grown to R1 — update A without regressing. | — |

Oracle workspaces may use only what a builder can see (the corpus, the console's
`describe()` and data, the framework docs), never the scenarios or `tests/`.

## build_r0

- `instruction.md`: the builder's brief.
- `tests/test.sh`: `verify.py build_r0` (scores A0 on the 10 R0 cases and keeps A0 + its results for the R1 diagnostics), then `advance.py` (reveals R1).
- `solution/`: the R0 oracle. See below.

`test.sh` is Harbor's entry for this step:

1. `python /tests/verify.py build_r0` — grades A0; exit 2 means an invalid run.
2. `/usr/local/libexec/redwood-advance` (`environment/advance.py`) — reveals R1 and seals `/tests`.
3. `chown` the verifier logs back to the host user so Harbor can archive them.

The checks themselves are in the task's `tests/` folder (see `tests/README.md`).

### build_r0 oracle

`solve.sh` copies `workspace/` over `/app/workspace`, builds the retrieval index
with `build_index.py`, and records a Pi session so session continuity checks pass.

| File | Layer | Holds |
|---|---|---|
| `build_index.py` | retrieval | **Selects** billing-relevant records from `/app/R` by visible metadata (title/path topic, published status), chunks them and embeds only those. Every record cited in `tests/provenance.json` must be selected. |
| `retrieval.py` | retrieval | Hybrid (vector + full-text) search over the index. |
| `tools.py`, `billing_rules.py` | actions | Console tools; code-enforced limits (R0 duplicate-credit cap $500) with a source comment. |
| `hooks.py` | guardrails | Deterministic guards around tool calls. |
| `AGENTS.md` | principles | Retrieval and handoff principles; no exact billing numbers. |
| `system_prompt.md` | identity | Role and authority. |
| `skills/` | procedures | `billing-investigation`: what to search and check, not the facts themselves. |
| `subagents/` | specialists | `policy-researcher` for conflicting sources. |

## maintain_r1

- `instruction.md`: continue the session; `/app/R` has new records, some superseding old ones.
- `tests/test.sh`: `verify.py maintain_r1` — scores A1 on the 10 R1 cases, then runs Q01/Q10 and the evolution report (diagnostic only).
- `solution/`: the R1 oracle. See below.

What changed in R1 (ΔR) is listed in `tests/delta_r1.json`, one entry per change with the layer it belongs in.

`test.sh` runs `python /tests/verify.py maintain_r1` and hands the logs back to the host user.

The reward is A1's score on the 10 R1 cases. The same run also writes, without
scoring, the evolution report (placement, health, drift, regression) into
`evidence.json`. See the task's `tests/README.md`.

### maintain_r1 oracle

`solve.sh` overlays `workspace/` on the A0 workspace (it does **not** start over),
rebuilds the index over `/app/R` (now R0 + R1), and appends to the same Pi session.

Each R1 change goes to the layer that owns it:

| Change | Layer | File |
|---|---|---|
| Duplicate-credit cap $500 → $250 | code-enforced limit | `billing_rules.py` |
| Confirm before credit/dispute writes (BILL-CONFIRM) | principle + guard | `AGENTS.md`, `hooks.py` |
| Contract / enterprise accounts: no Support credit, dispute to Billing Ops (BILL-AUTH) | action (code check) | `tools.py` (`_is_contract_account`) |
| Never repeat or store card numbers (BILL-PCI) | guard + principle | `hooks.py` (redacts), `AGENTS.md` |
| Credit balance from `credit_ledger` (BILL-LEDGER) | action | `tools.py` |
| Money as `{value, currency}` in R1 | action | `tools.py` |
| Newer published policy supersedes older text | principle + specialist | `AGENTS.md`, `subagents/policy-researcher.md` |
| Exact policy facts (402 quota, cached tokens, marketplace, incident window…) | retrieval | the rebuilt index, not the markdown files |

Only files that change are here; everything else is inherited from A0.
