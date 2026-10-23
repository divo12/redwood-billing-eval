---
name: usage-and-metering
description: Use for questions about token counts, billed vs expected usage, retries or cancelled streams being billed, cached tokens, error codes like 402/429 and whether failed requests are billed, usage export or Console totals that don't match, usage on the wrong month, or usage missing from the Console.
---
Goal: explain the developer's numbers from console records and current policy, and hand off only what the policy pages can't explain.

1. Get the facts. If not given, ask_developer once for what you need: ["org_id", "request_ids", "response_status", "error_code", "time_of_issue", "timezone", "billing_period", "checks_done"]. checks_done tells you their filters, how they counted, retries and timeouts.
2. Look up the account (note plan and billing_timezone), then get_usage for the request ids (or the invoice line). Read http_status, billed_tokens, cached_tokens, time in UTC and in the billing timezone, and billed_on_line. A request with no line on a direct invoice, or on a marketplace account, is not on a Redwood invoice line: say where it is billed per policy.
3. search_knowledge for the explanation, e.g. ["API error reference 402 quota_exceeded 429 rate limited", "how billed tokens are counted cache hits", "retries and streaming cancellation billing", "invoice periods billing timezone late usage prior-period", "usage missing from console threshold hourly batch", "embeddings batch usage delay"]. Use the current page when two disagree (e.g. an older free-tier page vs the newer error reference) and say which is current.
4. Compare their count to the explanation: billing month cut in the account's billing timezone (for the periods the close calendar says) vs their timezone, late usage billed next month as prior-period usage, project filters, prompt-only counting, client retries/timeouts, cancelled streams, tool-call payload, cache hits. Name the cause that fits their facts.
5. Decide:
   - Explained by a policy page: explain with their own numbers; no case, even if they call it a bug or double billing.
   - Still unexplained after those rules for specific request ids: route per the routing table (load billing-handoff) with the ids and what you checked.
   - Successful requests missing from the Console: compare the time since the request with the threshold the routing page gives for that request's date (and the endpoint's own delay page); before it, explain and say when to check again, no case; after it, route per policy.
   - Quota vs rate-limit errors: answer what the error means for their plan and when it clears, per the error reference and packages page.
   - They want correctly billed usage removed or credited: that's a credit/exception request; route per policy.
6. Reply with the answer first, then the detail and what they can change, quoting console figures.
