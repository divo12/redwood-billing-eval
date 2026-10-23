# Billing support agent: build and maintenance notes

Runtime files: `agent.py` (loop + auto-retrieval), `tools.py` (search, console reads/writes, custom
`delegate`), `hooks.py` (guardrails), `retrieval.py` (hybrid LanceDB search), `session.py`
(per-conversation consent/write ledger), `skills/billing-case-procedure`, `subagents/policy-researcher.md`,
`index/` (LanceDB table `chunks`), `console.py` (schema-adaptive console helpers: field names from
`describe()`, money as cents internally, duplicate original/copy analysis, credit-ledger balances). This `build/` directory is build-time only.

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
`retrieval.duplicate_limit_rules()` parses Support's per-line duplicate limits from the newest official
approval-matrix chunk (per price book: "rated on the 2027 price book: up to $X per duplicated line",
"earlier price book: up to $Y", or a flat "Up to $X per duplicated line") and each price book's
effective date from the close calendar ("The 2027 price book takes effect for usage from 1 March 2027").
`console.duplicate_limit_cents(invoice)` picks the limit by the invoice's period start. If wording
changes, update the regexes and re-run `build/checks.py`.

## Checks
`python build/checks.py` exercises tools and hooks against `development_billing()` (allowed and
blocked credits, consent, card redaction, escalation rules). Retrieval probes: see
`/app/framework/offline-rag/README.md`.

## R1 checklist
1. Diff record hashes vs `index/manifest.json`; read new/changed billing records (look for amended
   limits, new channel notes such as marketplace invoicing, superseded pages).
2. Rebuild the index; re-run `build/checks.py` and the probes; confirm the limit guardrail value.
3. If the console schema changed (`describe()`), update tool fields/enums in `tools.py`/`hooks.py`.

## R1 maintenance log (2027-04)
- Diff vs R0 manifest: 3,561 records added, 3 revised in place (routing page, payment/card page,
  approval matrix). Index rebuilt: 65.6k chunks, 13.6k embedded (billing scope).
- Policy changes absorbed: duplicate limit by price book ($250 on 2027-price-book invoices, $500 earlier;
  2027 book from March 2027 usage per the final close calendar, not the draft rollout plan's April);
  Support charter (no credit notes on contract accounts -> dispute + Billing Ops); credit/dispute the
  copy line (the one no usage record points to); confirmation required for credits and disputes only;
  "no" means nothing is written; card numbers never handled (not even last four), card changes are
  self-service; payment failures inside a published processor-incident window get no case; missing-usage
  threshold 60 min after the ENG-9120 cut-over; balances from `credit_ledger` (credit_grants frozen).
- Console format changes handled in code: money objects, `billing_account_id` on writable tables,
  `priority` on escalated cases (`normal`, per Support's handoff standard), `billing_timezone`.
- New tools: `get_credit_balance` (ledger), `date_helper` (timezones, business days).
- Drafts/proposals deliberately not applied: $1,500 limit, $1,000 refund authority, contract-account
  credit pilot, 2-hour missing-usage threshold, 2027 price book rollout plan.
