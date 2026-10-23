---
name: invoice-charge-review
description: Use when a developer says an invoice line is wrong, charged twice, unrecognized, or asks for a refund or credit on an invoice charge. Covers checking the console, deciding credit vs dispute vs handoff, and acting.
---
Goal: decide from console facts and current policy whether Support credits the line itself, opens a dispute and hands off, or only routes the request, then do it and tell the developer exactly what happened.

1. Identify. You need the org/account and the invoice (id or month). If missing, call ask_developer once with what is missing, e.g. ["org_id", "invoice_id", "invoice_month", "line_items", "request_ids"]. Don't ask for what they already wrote.
2. Look up. lookup_account (org or id). Note plan, billing_channel, billing_timezone, payment_method and contracts. If the invoice id is unknown, pick it from the account's invoices by period (the Console list sorts by issue date, not period); if several fit, ask.
3. Read the invoice. get_invoice. In duplicate_analysis:
   - Lines billing identical request ids where the usage records point to exactly one of them = console-confirmed duplicate. The line the usage points to is the ORIGINAL (leave it alone); the other is the COPY. Only the copy is credited or disputed, for its full amount.
   - Usage pointing to neither or both lines, or same amount/description with different request ids = not confirmed. Say so plainly, give each line's request id and amount, and don't credit.
   - Check already_credited and existing disputes so you don't act twice. Don't say a credit is likely before the console confirms the duplicate.
4. Get the policy. search_knowledge, e.g. ["support console duplicate charge credit dispute current version", "billing adjustments approval matrix duplicate-charge credit limit price book", "support charter what support may credit contract accounts"]. Use the current version of each page (v2 / newest published; superseded and draft pages are not policy). Find: (a) whether Support may credit on THIS account type (the charter decides: plan, signed contract); (b) the limit row and the condition that picks the limit for THIS invoice (e.g. which price book rates its usage period; Finance's close calendar says which usage each price book covers); note that passage's chunk_id.
5. Decide (from the passages, not memory):
   - Not a direct Redwood invoice (marketplace/reseller billing): Support issues nothing; tell them who bills and handles it, per the policy.
   - Confirmed duplicate, account where Support may credit, copy within the limit for this invoice: one credit note for the whole copy line, reason duplicate.
   - Confirmed duplicate but over that limit, or an account where Support may not credit (e.g. signed contract): no credit; open a dispute on the copy (reason duplicate_charge, its request ids), then escalate to the team the policy names with invoice, both line ids (original and copy), request ids, window in the account's billing timezone and the dispute id.
   - Not confirmed, or billed in error, or goodwill / exception / removal of a correct charge: no credit; route per the routing table (load billing-handoff).
6. Confirm. Credits and disputes need an explicit yes: confirm_with_developer with the exact action, line and amount, e.g. "Issue a credit note of $X for the duplicated line L2 (copy of L1, same request id R) on invoice I". If no: write nothing (no credit, dispute or case); say nothing was changed and they can come back.
7. Act. issue_credit_note with policy_chunk_id, policy_limit_cents and limit_basis (why that limit applies to this invoice); or open_dispute then escalate_case with dispute_id and full details. If a hook blocks, read why and follow that path; never retry a failed write blindly.
8. Reply. Say what the console showed (lines, request ids, amounts), what you did (credit note id and amount / dispute id and team), and what happens next per policy (e.g. when a credit is applied: search "after a credit note close calendar"), without promising a dispute outcome or date.
