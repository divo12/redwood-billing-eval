---
name: payments-and-credits
description: Use for failed or declined payments, autopay/card changes, overdue invoices, and credit balance or expiry questions. Covers payment-processor incident windows, card-data handling, and reading credit balances.
---
Failed card payment
1. lookup_account; ask_developer for invoice_id, error_code, time_of_issue and timezone if not given. Convert the failure time to UTC.
2. search_knowledge for the current payment-failure procedure and for payment-processor incident notices (status cards: "INC card payments failing"). Compare the failure time (UTC) with each published window; the start and end instants count as inside. The time decides, not the decline code.
3. Inside a window: no case. Name the incident, say the payment failed because of it, that it will be retried automatically, when the retries complete, that they need do nothing and no late fees apply. If this notice gives no completion date, derive the processor's usual schedule from ALL past payment-processor notices (search "INC card payments failing" and read each one's window end and "last retries completed" date). For each, call date_calculator(window_end_date, completion_date) and compare calendar_days and business_days; the measure that is the same across every notice is the schedule (a notice ending on a Friday shows whether weekends count). Apply it with date_calculator to this window's end date and state the date. Work this out once, carefully, and keep the same answer on follow-up questions unless you find a notice you had not read.
4. Outside every window, failed again after retries completed, or charged twice after retries: Billing Ops case with invoice number, decline code and failure time (load team-handoff).
5. Bank-transfer / invoice-pay accounts and overdue invoices: search the collections pages for their schedule.

Card changes and card data
- Never ask for, repeat, or store card digits (full, partial, last four, or spoken). If the developer shared one, tell them not to send card details to Support.
- Search the current card-update procedure; follow it (e.g. the customer changes the autopay card themselves in Console billing settings, no case). A card shared during a payment failure doesn't change how the failure is routed.

Credit balances
1. lookup_account: credits are computed from the credit ledger (sum of each grant's entries; expired grants excluded). Search the current credits policy for which table Support quotes.
2. Quote each grant's remaining balance and its own expiry date, plus the total. Mention that credits apply automatically to the next invoice if the policy says so.
3. Cash-out, refund, restoring expired credit or any exception: explain the policy, promise nothing, and open a Finance case only if the developer asks for it (load team-handoff).
