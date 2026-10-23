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
