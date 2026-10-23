# Knowledge index (maintainers)

Build-time only; the agent never reads `/app/R`.

    cd /app/workspace && python3 indexing/build_index.py /app/R

- `corpus.py` flattens each source record (fields, threads, comments) into text, prefixes every chunk with
  `[source id] title | status | updated`, and splits it into ~1,000-character windows (the embedding limit).
- Records in billing policy/process directories (`BILLING_DIRS`) or with billing-term density >= 2.5 per
  1k chars get dense vectors (table `billing`, ~5.2k chunks, ~3 min on 2 CPUs). Everything else is
  full-text only (table `corpus`). Vectors are cached in /tmp by chunk-text hash, so a rebuild only embeds
  changed chunks.
- `index/manifest.json` records each source's content hash: diff it against a new export to see what
  changed at a release.
- Runtime search (`retrieval.py`): exact record ids first, then a policy/procedure pass (Confluence and
  Drive pages, plus the curated billing directories), then everything else; reciprocal rank fusion of
  dense + full-text lists; at most 2 chunks per source.

After a rebuild, run the practice questions (`framework/retrieval_probes.json`) and re-check
`hooks.py` (SUPPORT_CREDIT_REASONS) and the skills against any changed policy pages.

## R1 maintenance log (2027 export)
- 3,561 new records; 3 revised in place (approval matrix: price-book-dependent duplicate limit; payment-failures page:
  incident-window failures get no case, card updates withdrawn; routing page: 60-minute missing-usage threshold after
  ENG-9120, handoff windows in the billing timezone). BILLING_DIRS extended with the charter, card-data standard,
  console record-format page, marketplace invoicing notes, API error reference, CS drafts and Finance x Support syncs.
- Console format (April 2027): money objects, `billing_account_id` on writable tables, `priority` on escalated cases,
  `credit_ledger` (balances; `credit_grants` frozen). tools.py reads field names from describe().
- Hooks: credits only on self-serve accounts without a contract (charter), only the copy line of a duplicate,
  consent for credits/disputes only (plain handoffs need none; nothing after a "no"), urgent priority blocked unless the
  routing policy says so, spoken-digit card numbers redacted.
