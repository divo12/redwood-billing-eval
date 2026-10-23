# Billing support agent: build and maintenance notes

Runtime files: `agent.py` (loop + auto-retrieval), `tools.py` (search, console reads/writes, custom
`delegate`), `hooks.py` (guardrails), `retrieval.py` (hybrid LanceDB search), `session.py`
(per-conversation consent/write ledger), `skills/billing-case-procedure`, `subagents/policy-researcher.md`,
`index/` (LanceDB table `chunks`). This `build/` directory is build-time only.

## Index
- `python build/build_index.py` (from the workspace) re-chunks `/app/R/sources` with `build/corpus.py`
  and rewrites `index/`. Vectors are cached by text hash in `/tmp/redwood_embed_cache.npz`, so only
  new or changed chunks are embedded.
- Every record is chunked (~900 chars, section heading carried, table headers repeated) and
  full-text indexed. Only billing-scoped records (`corpus.in_scope`: Support/Finance/pricing dirs, or
  billing-term density) get dense vectors; others have a zero vector and `in_scope=false`, and vector
  search pre-filters them out.
- Each chunk carries source path, title, status, date and an `authority` label
  (official / draft-proposal / archived / discussion / historical ticket) the agent uses to judge currency.
- `index/manifest.json` records per-record hashes: diff it against a new release to see what changed.

## Guardrails that read the index
`retrieval.support_duplicate_credit_limit_cents()` parses Support's per-line duplicate limit from the
newest official, non-draft chunk ("Up to $X per duplicated line"). If Finance changes the matrix, a
rebuild updates the guardrail; if the wording changes, update `LIMIT_PATTERN`.

## Checks
`python build/checks.py` exercises tools and hooks against `development_billing()` (allowed and
blocked credits, consent, card redaction, escalation rules). Retrieval probes: see
`/app/framework/offline-rag/README.md`.

## R1 checklist
1. Diff record hashes vs `index/manifest.json`; read new/changed billing records (look for amended
   limits, new channel notes such as marketplace invoicing, superseded pages).
2. Rebuild the index; re-run `build/checks.py` and the probes; confirm the limit guardrail value.
3. If the console schema changed (`describe()`), update tool fields/enums in `tools.py`/`hooks.py`.
