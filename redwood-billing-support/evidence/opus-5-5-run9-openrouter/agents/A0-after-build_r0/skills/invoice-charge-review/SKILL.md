---
name: invoice-charge-review
description: Use when a developer says an invoice line is wrong, charged twice, unrecognized, or asks for a refund or credit on an invoice charge. Covers checking the console, deciding credit vs dispute vs handoff, and acting.
---
Goal: decide from console facts and current policy whether Support credits the line itself, opens a dispute and hands off, or only routes the request, then do it and tell the developer exactly what happened.

1. Identify. You need the org/account and the invoice (id or month). If missing, call ask_developer once with what is missing, e.g. ["org_id", "invoice_id", "invoice_month", "line_items", "request_ids"]. Don't ask for what they already wrote.
2. Look up. lookup_account (org or id). Note billing_channel, plan, payment_method, contracts. If the invoice id is unknown, pick it from the account's invoices by period; if several fit, ask.
3. Read the invoice. get_invoice. Look at duplicate_analysis:
   - Lines billing the identical request ids = console-confirmed duplicate. The later line is the duplicated one.
   - Same amount or description with different request ids = not a confirmed duplicate. Say so plainly, give each line's request id and amount, and don't credit.
   - Check already_credited and existing disputes so you don't act twice.
4. Get the policy. search_knowledge, e.g. ["support console duplicate charge credit dispute", "billing adjustments approval matrix support limit per line", plus the developer's situation]. Find the current published/final page that says what Support may credit and the limit, and the routing page for anything else. Ignore archived help articles and draft proposals for the rule (mention them only if the developer cites them). Note the chunk_id of the passage stating the limit.
5. Decide (from the passages, not memory):
   - Not a direct Redwood invoice (marketplace or reseller billing): Support issues nothing; tell them who bills and handles it, per the policy.
   - Confirmed duplicate within Support's limit: one credit note for the whole duplicated line, reason duplicate.
   - Confirmed duplicate over the limit: no credit; open a dispute on the duplicated line (reason duplicate_charge, its request ids), then escalate to the team the policy names with invoice, line and dispute id.
   - Not confirmed, or the developer says billed in error, or wants goodwill / an exception / removal of a correct charge: no credit; route per the routing table (load billing-handoff).
6. Confirm. confirm_with_developer with the exact action: "Issue a credit note of $X on invoice I line L (duplicate of line K, same request id R)" or "Open a dispute on I line L and hand it to <team>". If no: don't act; explain the alternative.
7. Act. issue_credit_note with policy_chunk_id and policy_limit_cents from step 4; or open_dispute then escalate_case with dispute_id and full details. If a hook blocks, read why and follow that path; never retry a failed write blindly.
8. Reply. Say what the console showed (lines, request ids, amounts), what you did (credit note id and amount / dispute id and team), and what happens next without promising an outcome or date. If they ask when a refund lands, answer only from a current source; otherwise say the team confirms it.
