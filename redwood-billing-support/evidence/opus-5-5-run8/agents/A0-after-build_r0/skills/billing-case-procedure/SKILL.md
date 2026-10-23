---
name: billing-case-procedure
description: Load before any billing action or handoff - a duplicate or wrong charge, refund, credit or SLA/service-credit request, dispute, failed payment or card change, marketplace/reseller billing, or usage that doesn't match. Gives the ordered checks for credit vs dispute vs handoff.
---
# Billing case procedure

Rules (limits, owners, conditions) come from the pages you retrieve, not from this procedure.

## 1. Classify the ask
- Explain-only: how tokens/retries/streams are billed, console lag, export columns, plan limits, balances. Answer from the pages and stop; no case, even if the developer calls it a bug.
- Duplicate/incorrect charge on an invoice -> step 3.
- Refund, credit, goodwill, waiver, SLA/service credit, exception, "already approved by X" -> step 4.
- Payment failure, card/autopay change, receipts, overdue/dunning -> step 5.
- Usage missing or tokens that don't match after the documented rules -> step 6.

## 2. Gather facts (one `ask_developer` call)
Ask only what is still missing: org_id, invoice_id/invoice_month, line_items/amounts, request_ids, response_status, time_of_issue + timezone, plan/contract, error_code, checks_done. Then `lookup_account` and note plan, billing_channel, payment_method and contracts.
- If billing_channel is a marketplace or the account is reseller-billed, search how that channel is handled before anything else; Support issues no credit or dispute on invoices Redwood didn't issue.

## 3. Duplicate or wrong charge
1. `get_invoice`. A duplicate is confirmed only when two lines on the same direct invoice bill the same request ids (`lines_billing_identical_request_ids`). Same description or amount is not enough.
2. Search the current Support credit limit for duplicates (Finance approval matrix) and the credits/disputes page.
3. Confirmed + within limit: confirm with the developer the exact line and full amount, then `issue_credit_note` (reason `duplicate`, the duplicated line, full line amount, one note). Tell them the amount credited. No handoff needed.
4. Confirmed + over the limit: never split. Confirm, `open_dispute` (reason `duplicate_charge`, the duplicated line, its request ids), then `escalate_case` to the owning team with invoice, line, request ids and the dispute id.
5. Not confirmed (different request ids / no second line): no credit. Explain what the console shows, collect intake details and hand off per the routing page.
6. Check `existing_credit_notes`/`existing_disputes` first so nothing is done twice.

## 4. Credits, refunds, SLA, exceptions
Search the routing page and approval matrix for who owns this exact ask (direct invoice billed in error vs correct charge; self-serve vs signed contract; contract the console doesn't show; someone "already approved" it). Support never issues these itself. Explain the general rule without promising an amount. Before handing off, `ask_developer` for what the owner needs and you lack (amounts, request_ids, time_of_issue, contract, checks_done). Then confirm the handoff and `escalate_case` to the owner with the ask, the amount if given, and why.

## 5. Payments
Search the payment/collections pages and incident status cards for the decline code and date (a known incident may already explain it). Never take a full card number; only the last four digits. Route per the page.

## 6. Usage/meter
Apply the documented token, retry, stream-cancel and export rules first and check the usage records. Hand off only what those rules don't explain, to the team the routing page names.

## 7. Close
Report what you did (ids, amounts, team) and the next step and owner. If you handed off, say the team has the case; don't promise outcome or timing.
