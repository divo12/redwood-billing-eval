---
name: payments-and-credits
description: Use when a developer asks about a failed or declined payment, changing their card, overdue invoices, late fees or suspension, or their credit balance, credit expiry, promo/workshop codes, or wants credits refunded, cashed out or restored.
---
Failed payment
1. Get the invoice, the decline code, and when it failed with the timezone they used (`ask_developer` once for what's missing). Convert the time to UTC.
2. `search_knowledge` for the payment-processor incident procedure and for incident status cards for payment failures around that date (e.g. "INC card payments failing <month year>"). Read each card's window (UTC).
3. Inside a published window (the boundaries count as inside): the time decides, not the decline code. Name the incident, say the payment will be retried automatically and when retries complete, that they need do nothing, and that no late fees apply. No case. If this incident's notice gives no completion date, work it out from the usual schedule: search the earlier payment-processor incident notices, compare each window's end with its "last retries completed" date, apply that gap to this window's end, and say it is based on the usual schedule.
4. Outside every window, failed again after retries completed, or charged twice after retries: open the case the routing page names with invoice, decline code and failure time.
5. Card changes: Support never takes or repeats card numbers and doesn't change payment methods. Point them to Console billing settings; no case. If they shared a number, tell them not to send card details.

Overdue, late fees, suspension
- Use the current collections/dunning pages (check the effective year and whether the account pays by card or by invoice) and the account's invoice status from the console. Payments failed in an incident window are not overdue until retries finish.

Credit balance and expiry
1. `account_overview`: quote the balance from `credits` (the credit ledger), the balance and expiry date of each grant, not only the total. Confirm with `search_knowledge` which table policy says to quote if anything looks inconsistent.
2. Cash-out, refund of credits, restoring an expired credit, or another exception: explain the policy, promise nothing, and route the ask as the credit-ledger/routing pages say. Promo or workshop codes: follow their page (usually no case).
