# Knowledge index: build and maintenance (builder notes, not loaded by the agent)

Rebuild: `cd /app/workspace && python3 build_index/build.py` (reads /app/R, writes `knowledge/`).

- `chunking.py` renders each R record (any source format) into title/status/date/authority metadata and
  body fields, splits bodies at headings/paragraphs (tables kept whole, header row repeated), ~900-char
  children. Parent passage = child + neighbours up to 2,600 chars; Confluence/Drive pages up to 6,500
  chars are returned whole (a truncated routing page once hid "don't quote a figure from the contract").
- Dense scope (`in_dense_scope`): policy spaces (support-process, known issues, pricing-and-packaging,
  finance-and-legal, pricing guidance, status-page cards, Finance drive) + any non-GitHub/HubSpot record
  with >= 5 billing-keyword hits, plus short Slack threads (<2.5k chars) and short call records (<4k):
  pages point to decisions announced there (e.g. case priority, credit-ledger cut-over).
  R1: ~1.5k records / ~19.6k chunks; ~11 min to embed from scratch, seconds when cached.
  Everything else goes to the lexical-only `archive` table.
- Vectors are cached by sha1(embed_text) in `.cache/vectors.npz`: an R1 rebuild embeds only new/changed text.
- `knowledge/manifest.json` holds per-record content hashes; diff it against a new R to see additions.
- `authority` is a coarse label (published / draft / archived / ticket / discussion / crm); the agent
  still reads status and text. Drafts/archived pages are kept on purpose: the agent must see conflicts.
- Hooks read Support's duplicate-credit limit from the newest published approval-matrix row at runtime
  (`knowledge.duplicate_credit_limit_cents(period_start)`): per price book when the row differs by book,
  with the book's effective date from Finance's close calendar; unknown -> strictest limit. If Finance's
  wording changes, re-check those regexes (tests/test_wiring.py asserts $500 for 2026, $250 for 2027).
- Console access is schema-driven (`console.py`, from `describe()`): R1 changed money to
  {"value","currency"} objects, writable tables to `billing_account_id`, added case `priority`,
  `billing_timezone` and the `credit_ledger` (balances are quoted from the ledger only).

## R1 changes (2027 policy layer)
Revised in place: routing page (processor-incident failures get no case; missing-usage 60 min after
ENG-9120 cut-over 2027-03-01; windows in billing timezone), payment page (card updates withdrawn),
approval matrix ($250/line on 2027 price book, $500 earlier). New: v2 duplicates page (credit the copy,
not the original), Support Charter 2027 (credits only on self-serve; none on contract accounts),
Confirm-Before-You-Write standards, record-format changes, credit ledger, processor incidents, card
data standard (never repeat any digits), API error reference (Free limit = 402), close calendar.
Traps: drafts (refund $1,000, contract pilot, 2-hour threshold, price-book rollout), Slack opinions
(urgent on everything, telemetry after 10 min, cache hits 50%), marketplace memo $1,000 (Partnerships).

Checks after a rebuild: `python3 tests/test_wiring.py` (no support-model calls), then the probes in
/app/framework/retrieval_probes.json via a small script over `knowledge.Knowledge().search`.
