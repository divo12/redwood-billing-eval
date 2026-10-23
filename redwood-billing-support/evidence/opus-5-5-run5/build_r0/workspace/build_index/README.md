# Knowledge index: build and maintenance (builder notes, not loaded by the agent)

Rebuild: `cd /app/workspace && python3 build_index/build.py` (reads /app/R, writes `knowledge/`).

- `chunking.py` renders each R record (any source format) into title/status/date/authority metadata and
  body fields, splits bodies at headings/paragraphs (tables kept whole, header row repeated), ~900-char
  children. Parent passage = child + neighbours up to 2,600 chars; Confluence/Drive pages up to 6,500
  chars are returned whole (a truncated routing page once hid "don't quote a figure from the contract").
- Dense scope (`in_dense_scope`): policy spaces (support-process, known issues, pricing-and-packaging,
  finance-and-legal, pricing guidance, status-page cards, Finance drive) + any non-GitHub/HubSpot record
  with >= 5 billing-keyword hits. ~700 records / ~9k chunks / ~5 min to embed from scratch.
  Everything else goes to the lexical-only `archive` table.
- Vectors are cached by sha1(embed_text) in `.cache/vectors.npz`: an R1 rebuild embeds only new/changed text.
- `knowledge/manifest.json` holds per-record content hashes; diff it against a new R to see additions.
- `authority` is a coarse label (published / draft / archived / ticket / discussion / crm); the agent
  still reads status and text. Drafts/archived pages are kept on purpose: the agent must see conflicts.
- Hooks read Support's duplicate-credit limit from the newest published approval-matrix chunk at runtime
  (`knowledge.duplicate_credit_limit_cents`); if Finance's matrix wording changes, check that regex.

Checks after a rebuild: `python3 tests/test_wiring.py` (no support-model calls), then the probes in
/app/framework/retrieval_probes.json via a small script over `knowledge.Knowledge().search`.
