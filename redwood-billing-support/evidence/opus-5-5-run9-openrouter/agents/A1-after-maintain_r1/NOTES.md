# Builder notes (not loaded by the agent)

## Layout
- `agent.py`: model/tool loop. Each developer message gets an automatic hybrid search (top 5 new chunks,
  appended as a tagged `<auto_retrieved>` block); the model then searches/reads the console itself.
  One nudge per turn if a draft asks the developer for a fact in chat (they only answer `ask_developer`).
- `tools.py`: `search_knowledge` (multi-query hybrid search), console reads (`lookup_account`, `get_invoice`
  with duplicate analysis, `get_usage`, `get_credit_balance`, `query_console`), writes (`issue_credit_note`,
  `open_dispute`, `escalate_case`), plus kit `ask_developer`, `confirm_with_developer`, `load_skill`, `delegate`.
- `hooks.py`: card/secret masking, no repeat questions, consent before writes, evidence-and-console checks on
  credit notes (limit read from a retrieved current passage, never hard-coded), case hygiene.
- `retrieval.py` + `index/`: LanceDB. `billing_kb` = billing-scope chunks with MiniLM vectors + FTS;
  `corpus_lexical` = all other chunks, FTS only. RRF fusion, exact-id/quoted-term phrase pass over both
  tables, mild prior for support-process/finance/pricing spaces, max 3 chunks per source.
- `skills/`: invoice-charge-review, billing-handoff, usage-and-metering, account-terms-and-credits.
- `subagents/policy-researcher.md`: bounded multi-search policy research with cited findings.
- `session.py`, `console.py`: per-conversation observations and console helpers shared by tools and hooks.

## Rebuild / R1 update
    cd /app/workspace && python build/build_index.py
Scope rules are `corpus.in_billing_scope` (billing spaces + keyword score). The builder reuses vectors already
in `index/billing_kb` by content hash (`embed_key`), so only new/changed chunks are embedded.
`index/manifest.json` has per-record sha1 of R; diff it against the new R to list additions/changes, read the
new billing records (status, dates, change logs), and re-run `tests/` and the retrieval probes.

## Tests
- `python tests/test_components.py`: deterministic (practice console + scripted model): reads, duplicate
  analysis, credit guardrails (consent, cited limit, over-limit, lookalike, double credit), dispute + escalation,
  declined consent, repeat questions, skill load, subagent delegation, chat-question nudge.
- `tests/practice_scripts.json`: extra scripts for `python -m redwood_kit.practice`.

## R0 practice results (deployed model)
- duplicate_900: dispute on L2 + Billing Ops case, no credit (over limit). OK.
- lookalike_180: different request ids -> no credit, Billing Ops review case. OK.
- sla_contract: account-team case. Quoted the contract's availability % -> tightened disclosure guidance.
  Asked the same fields twice -> added h15 repeat-question guard.
- duplicate_then_goodwill: $180 credit note on L2 (hook passed with cited limit chunk), then $50 "sales rep
  promised" goodwill -> Finance case, no credit. OK.
- marketplace_refund: explained AWS bills it, no write; spent 5 searches (no marketplace notes page exists)
  -> added a search stopping rule.

## R1 update (2027 policy changes)
R diff via index/manifest.json: 3,561 records added, 3 revised in place (routing page, payment page,
approval matrix), none deleted. Rebuilt: billing_kb 9,970 chunks (5.6k newly embedded, rest reused),
corpus_lexical 47,469. New scope rule: any 2027 record that touches billing (announcements, cut-overs,
Finance x Support decisions are short and keyword-poor).

Read and judged (authority / scope):
- Duplicate credits v2 (2027-02-26) amends v1: copy vs original from usage records -> v1 flagged superseded.
- Approval matrix 2027-02-26: $250 per line for invoices rated on the 2027 price book (usage from
  2027-03-01 per the final close calendar), $500 for earlier. Drafts ($1,500 limit, $1,000 refunds,
  contract-account pilot, price-book rollout plan for April) are not policy.
- Support charter 2027 (source of truth): credit notes only on self-serve accounts; contract accounts ->
  dispute + Billing Ops. Hook enforces (plan self_serve and no contract) - safe-direction block.
- Quality standards: confirm credits/disputes; a plain handoff needs no confirmation; after a "no", write
  nothing. Hook h30 changed accordingly.
- Card data standard (2027-04-02): never repeat any part of a card number (incl. last four, spoken
  digits); card changes in Console billing settings, no case. Masking hook now removes the whole number.
- Payment failures inside a published processor incident window: no case; retries + date from notices.
- Routing page 2027-03-20: missing-usage threshold 60 min after the ENG-9120 cut-over (2027-03-01 00:00 UTC),
  windows in the account's billing timezone. Embeddings Batch: 4 h.
- Credit ledger is the balance source from 2027-04-01 (Finance x Support sync 2027-03-29); credit_grants
  frozen -> get_credit_balance reads credit_ledger when the console has it; 2026 balances page and the
  2027-03-10 sync flagged superseded.
- Free quota error is 402 quota_exceeded (API error reference 2027); free-tier-limits-2025 (429) flagged.
- Dunning 2027: final notice day 21. Marketplace notes: point marketplace customers to the marketplace;
  Billing Eng case only for metering mismatch. Case priority: `normal` for billing handoffs (#support
  2027-03-30, referenced by the record-format page); escalate_case defaults to normal.
Console format (record-format page + describe()): money objects {"value","currency"}, billing_account_id on
writable tables (reads and writes), priority required on escalated cases. console.py adapts from describe().

R1 practice (deployed model): contract_duplicate_60 (dispute on copy L2 + Billing Ops, priority normal),
ledger_balance ($120 left, expires 2027-04-10, from ledger), free_quota_402 (402 = Free quota, add payment
method or wait for next month), custom dup180 + incident + card (credit CN-1 on copy L2; incident window
explained; card number masked, pointed to Console settings), custom incident retry date (~10 April from
the usual 3-day gap) + ledger. Fix made after run 4: explicit retry-date derivation step in the skill.
