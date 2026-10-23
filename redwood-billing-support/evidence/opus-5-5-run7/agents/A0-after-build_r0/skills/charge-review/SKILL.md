---
name: charge-review
description: Use when a developer says an invoice line is duplicated, wrong, unrecognized, or asks for a refund or credit on a charge. Decides credit vs dispute vs handoff vs explanation from console facts and current policy.
---
Inputs: the developer's org or account id, the invoice (id or month), the lines and amounts they mean, request ids if they have them.

1. Identify the account. If you have neither org nor account id, ask_developer for org_id (plus invoice_id/invoice_month and line_items/amounts/request_ids in the same call if not given). lookup_account.
2. Check the billing channel and plan on the account.
   - Not billed directly by Redwood: search_knowledge for the procedure for that exact channel. A cloud marketplace (aws_marketplace, gcp_marketplace) and a reseller are different channels with different pages; apply only the one matching the console's billing_channel and don't borrow steps from the other. Support usually cannot credit or dispute these; tell the developer who bills them and where adjustments go. Do not open a credit or dispute.
3. Find the invoice: get_invoice (use lookup_account's invoice list to map a month to an id). Read console_confirmed_duplicates and same_amount_but_different_requests. If the developer gave request ids, get_usage them.
4. Classify:
   a. Console-confirmed duplicate: two lines on the same direct invoice billing the identical request ids. The duplicated line is the later line (duplicated_lines); credit that one, never the original.
   b. Look-alike: same amount/description but different request ids, or no second line. NOT a confirmed duplicate: say so plainly, with the console evidence (each line's request ids and times).
   c. Wrong rate/quantity/product, usage they don't recognize, retries/cancelled streams, token-count mismatch: search the token/usage pages first; if they explain it, explain and stop.
   d. Request to waive or reduce a correct charge (goodwill, exception, SLA credit): not Support's to issue. Load team-handoff.
5. For (a), search_knowledge for Support's current credit limit for duplicate charges and how it applies (per line, full line amount, no splitting). Read the approving document's status/date; drafts and proposals do not change the limit. If sources disagree on the limit (e.g. a draft, a thread or the developer cites a different figure), delegate to policy-researcher with the question and the dates before deciding. Compare the duplicated line's full amount with the limit.
   - Within limit: confirm_with_developer "Issue a credit note of $X (reason duplicate) on line L of invoice I". If yes: issue_credit_note for the full line amount with that reason, memo naming the request ids. Tell the developer the credit note id and amount. No handoff needed.
   - Over limit: do not credit and do not split. confirm_with_developer that you will open a dispute on line L and hand it to the owning team. If yes: open_dispute (reason duplicate_charge, the line, the request ids, submit=true), then load team-handoff and escalate with the dispute id.
6. For (b) and unexplained (c): gather intake (invoice, lines, UTC window, request ids, what they checked) with ask_developer, then load team-handoff for the owning team named in the routing policy.
7. Never promise a refund date or dispute outcome. If a write is rejected, read get_invoice to see the state before saying anything.
