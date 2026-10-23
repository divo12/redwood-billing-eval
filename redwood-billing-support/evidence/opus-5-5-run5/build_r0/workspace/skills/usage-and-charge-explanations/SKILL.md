---
name: usage-and-charge-explanations
description: Use when a developer asks why they were charged what they were, why their token count or usage export differs from the invoice or their own count, about retries, cancelled streams, cached tokens, free-tier limits, 429 quota errors, credit balances, pricing or plans.
---
Goal: explain the charge from the account's own records and the current documented behaviour, and hand off only what the documents don't explain.

1. Get identifiers with `ask_developer` if missing: `org_id`, and as relevant `invoice_id`/`billing_period`, `request_ids`, `response_status`, `time_of_issue`, `timezone`, `checks_done` (filters, how they counted, client retries/timeouts).
2. Read the console: `lookup_account` (plan, channel, credit grants), then `get_invoice` and/or `get_usage` for the named requests or lines. Note each request's HTTP status, billed and cached tokens, time (UTC) and whether it has an invoice line.
3. `search_knowledge` for the documented behaviour behind what you see (e.g. "how billed tokens are counted", "retries and stream cancellation billing", "usage export columns and filters", "free tier limit 429", "credit balances expiry", "console usage delay"). Use current published pages; if an older page conflicts, say which page is current.
4. Compare: apply the documented rules to the developer's own checks (timezone used to cut the month, project filters, counting prompt tokens only, client retries or timeouts, cancelled streams, cached tokens). Show the specific cause you can see in their records.
5. Answer with the cause, the evidence (request ids, times, statuses, amounts as the console shows them) and the fix or mitigation the documents give.
6. Hand off (use the billing-handoff skill) only when the documented rules don't explain the numbers, or the developer asks for something Support can't do (a credit or waiver for correctly billed usage, meter correction, missing usage after the documented delay).
