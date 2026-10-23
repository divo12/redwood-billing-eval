---
name: charge-review
description: Use when a developer says an invoice line is duplicated, wrong, unrecognized, or asks for a refund or credit on a charge. Decides credit vs dispute vs handoff vs explanation from console facts and current policy.
---
Inputs: the developer's org or account id, the invoice (id or month), the lines and amounts they mean, request ids if they have them.

1. Identify the account. If you have neither org nor account id, ask_developer for org_id (plus invoice_id/invoice_month and line_items/amounts/request_ids in the same call if not given). lookup_account.
2. Check billing channel, plan and has_signed_contract on the account.
   - Not billed directly by Redwood: search_knowledge for the procedure for that exact channel (cloud marketplace vs reseller are different pages; apply only the one matching billing_channel). Support credits/disputes nothing there; tell the developer who bills them and where refunds/credits/invoices go. Open a case only if the current page says so (e.g. a metering mismatch).
3. get_invoice (map a month to an id from lookup_account's invoice list). Read console_confirmed_duplicates (original_line vs copy_lines, decided by where the usage records point), same_requests_but_unconfirmed, and same_amount_but_different_requests. get_usage for request ids the developer gave.
4. Classify:
   a. Console-confirmed duplicate: act only on the copy line, never the original.
   b. Look-alike (same amount/description, different request ids), no second line, or usage pointing to neither/both lines: NOT a confirmed duplicate. Say so plainly with the console evidence. Never say a credit is likely before the console confirms a duplicate.
   c. Wrong rate/quantity/product, unrecognized usage, retries/cancelled streams, token mismatch: search the usage/token pages; if they explain it, explain and stop.
   d. Waive/reduce a correct charge (goodwill, exception, SLA credit, refund): not Support's to issue. Load team-handoff.
5. For (a), search_knowledge for what Support may do now: the Support charter (which accounts Support may credit at all), the credits-and-disputes page, and Finance's approval matrix for the per-line limit. The limit can depend on how the invoice was rated (e.g. which price book applies to the invoice's usage period): search the Finance close calendar for that, using the invoice period. Drafts, proposals and threads change nothing.
   - Support may credit this account and the copy line's full amount is within the limit: confirm_with_developer "I'll issue a credit note of $X (full amount) on the duplicated line L of invoice I." Only after yes: issue_credit_note (reason duplicate, memo with request ids). Tell them the credit note id and amount, and when it applies if the page says. No handoff.
   - Over the limit, or an account Support may not credit (e.g. contract/enterprise per the charter): do not credit or split. confirm_with_developer "I'll open a dispute on line L of invoice I (duplicate charge, request ids R) and hand it to Billing Ops." After yes: open_dispute, then load team-handoff and escalate with the dispute id.
   - If the developer says no: write nothing (no credit, dispute or case); say nothing was changed and they can come back.
6. For (b) and unexplained (c): gather intake (invoice, both lines, window in the account's billing timezone, request ids, what they checked) with ask_developer, then load team-handoff.
7. Never promise a dispute outcome or date. If a write is rejected, read get_invoice to see the state before saying anything.
