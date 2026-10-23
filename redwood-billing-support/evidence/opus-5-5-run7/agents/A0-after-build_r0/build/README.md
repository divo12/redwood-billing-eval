# Builder notes (not loaded by the agent)

## Design (R0)
- **Retrieval**: routed hybrid RAG over LanceDB `index/`.
  - `billing` table: chunks of 571 billing-relevant records (support-process, pricing, finance, status cards,
    billing-labelled Jira, finance_billing email, billing-heavy Slack #support) with MiniLM vectors + FTS.
  - `archive` table: all other records' chunks, FTS only (no vectors), searched for exact ids or on request.
  - Query: dense top-30 + dense restricted to policy directories (top-12) + FTS top-30 (+ id-only FTS on both
    tables), RRF (c=60), small prior for policy directories, +0.05 for literal id match, max 2 chunks/record.
  - Chunks (~1100 chars) carry a provenance header (kind | title | status | date); continuation chunks repeat the
    section heading and markdown table header. Status gets ARCHIVED / DRAFT/PROPOSAL flags from labels/titles.
- **Agent** (`agent.py`): tool loop; per developer message an auto-retrieval system note (top 5); max 12 tool
  rounds per message; one nudge if a reply asks the developer for facts in chat (they only answer ask_developer).
- **Tools**: search_knowledge, lookup_account, get_invoice (duplicate analysis: identical request_id sets),
  get_usage, console_query/console_schema, issue_credit_note, open_dispute, escalate_case (+ kit tools).
  Enums are validated against runtime describe(); `extra_fields` passes new required fields if the schema grows.
- **Hooks**: card-number/CVV redaction on developer messages; block card data in writes; writes need latest
  confirm_with_developer == yes in the same developer turn; one credit per approval and per line; no writes
  after escalation; a failed write blocks further writes until a console read.
- **Skills**: charge-review (duplicate/wrong charge/refund decision), team-handoff (routing + case contents).
- **Subagent**: policy-researcher (search_knowledge only) for conflicting/multi-part policy questions.
- No billing figures live in prompts/skills: limits, approvers and teams come from retrieved chunks.

## Key R0 policy sources (for checking retrieval, not copied into prompts)
approval matrix (finance drive, final 2026-02-03), support-console credits & disputes (2026-02-05), billing case
routing (2026-02-06), reseller disputes (2026-04-02), payment failures (2026-04-20; asks Support to take card numbers:
hook keeps PANs out of cases), credit balances, free tier, usage export, retries/streaming, dunning, status cards.
Traps: archived help-center page, $1,500 limit DRAFT proposal, v0.3 RACI draft. "Marketplace invoicing notes" are
referenced but absent in R0.

## Practice (R0, 5/5 used)
duplicate_180 credit ok; duplicate_900 dispute+billing_ops ok; custom goodwill_selfserve -> finance ok (fixed: tell
self-serve no SLA); custom card_payment -> billing_ops, no PAN ok; marketplace ok (fixed: don't mix reseller steps).

## R1 checklist
1. `python build/build_index.py --no-embed` for counts; diff `/app/R` against `build/inventory_R0.json`
   (new/changed files); read new billing records (policy changes, approvals of drafts, marketplace notes).
2. Check `console.describe()` vs `/app/framework/client_api/schema.json` changes (new tables/fields/enums/channels).
3. Rebuild: `python build/build_index.py` (embed cache in build/embed_cache.json: only changed chunks embed).
   Extend CORE_DIRS if new policy directories appear.
4. `python build/test_wiring.py` and `python build/probe.py "<question>"` for new scenarios; practice scripts in
   build/practice/.
