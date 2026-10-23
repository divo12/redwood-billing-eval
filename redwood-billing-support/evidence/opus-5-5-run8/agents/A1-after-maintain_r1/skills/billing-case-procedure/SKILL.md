---
name: billing-case-procedure
description: Load before any billing action or handoff - a duplicate or wrong charge, refund, credit or SLA/service-credit request, dispute, failed payment or card change, credit balance, marketplace/reseller billing, quota/usage errors, or usage that doesn't match. Gives the ordered checks for credit vs dispute vs handoff vs answer-only.
---
# Billing case procedure

Rules (limits, owners, thresholds, effective dates) come from the pages you retrieve, not from this procedure.

## 1. Classify the ask
- Explain-only: token counting, retries/cancelled streams, console lag, export columns, error codes and quotas, plan limits, credit balances, promo/workshop codes, card changes, typical credit amounts. Answer from the pages and stop; no case, even if the developer calls it a bug.
- Duplicate/incorrect charge on an invoice -> step 3.
- Refund, credit, goodwill, waiver, SLA/service credit, exception, "already approved by X", cashing out or restoring credits -> step 4.
- Payment failure, overdue/dunning, receipts -> step 5.
- Usage missing or tokens that don't match after the documented rules -> step 6.
- Plan, limit or reservation changes, MDF, reseller/marketplace bills: search who owns it; Support doesn't change these.

## 2. Gather facts (one `ask_developer` call)
Ask only what is still missing: org_id, invoice_id/invoice_month, line_items/amounts, request_ids, response_status, time_of_issue + timezone, plan/contract, error_code, checks_done. Then `lookup_account`: note plan, has_signed_contract, billing_channel, payment_method, billing_timezone.
- Marketplace or reseller-billed: search how that channel is handled; Support issues no credit or dispute on invoices Redwood didn't issue.

## 3. Duplicate or wrong charge
1. `get_invoice`. Use `date_helper` for any timezone, billing-month or business-day arithmetic. Use `duplicate_analysis`: a duplicate is confirmed only when two lines on the same direct invoice bill the same request ids AND the usage records point to exactly one of them (the original). Act only on the copy. Same description or amount is not enough.
2. Search the current credits/disputes page and the Support charter, and read `support_duplicate_credit_limit` for this invoice (it depends on the invoice's price book; check the approval matrix if unsure).
3. Support may credit only if the charter allows it for this account type (check self-serve vs signed contract) AND the copy line is within the limit: confirm the copy line and full amount with the developer, then `issue_credit_note` (reason `duplicate`). Tell them the amount credited and when credits apply (search the close calendar if asked).
4. Confirmed but over the limit, or an account Support may not credit: never split. Confirm the dispute, `open_dispute` (reason `duplicate_charge`, the copy line, request ids, window in the account's billing timezone), then `escalate_case` to Billing Ops with invoice, both line ids (original and copy), request ids and dispute id.
5. Not confirmed: no credit, no promise. Explain what the console shows, collect intake details, hand off per the routing page.
6. Check `existing_credit_notes`/`existing_disputes` first so nothing is done twice.

## 4. Credits, refunds, SLA, exceptions
Search the routing page, approval matrix and charter for who owns this exact ask (billed in error vs correct charge; self-serve vs signed contract; contract the console doesn't show). Support never issues these. Explain the general rule without promising an amount. `ask_developer` for what the owner needs (ask, amount, why, request_ids, time_of_issue, contract), then `escalate_case` to the owner.

## 5. Payments
Never take any card details; card changes are self-service (search where). For a failed payment, get the failure time (UTC) and decline code, search published payment-processor incident notices and compare the time with each window: inside a window -> explain, say when retries complete, no case. If that notice doesn't state the completion date, derive the processor's usual schedule from earlier payment-processor notices (window end vs last-retry date, in business days; use `date_helper`) and apply it to this window. Outside every window (or the page lists another exception) -> Billing Ops case with invoice, decline code and time.

## 6. Usage/meter
Apply the documented token, retry, stream, batch and export rules and check the usage records. For missing usage, compare the request time with the current threshold for that endpoint and date (cut-overs change it); before the threshold explain when to check again, no case. Hand off only what the rules don't explain, to the team the routing page names.

## 7. Close
Report what you did (ids, amounts, team) and the next step and owner. If you handed off, say the team has the case; don't promise outcome or timing.
