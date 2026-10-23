---
name: usage-and-metering
description: Use for questions about token counts, billed vs expected usage, retries or cancelled streams being billed, cached tokens, failed requests, usage export or Console totals that don't match, or usage missing from the Console.
---
Goal: explain the developer's numbers from console records and current policy, and hand off only what the policy pages can't explain.

1. Get the facts. If not given, ask_developer once for what you need: ["org_id", "request_ids", "response_status", "time_of_issue", "timezone", "billing_period", "checks_done"]. checks_done tells you their filters, how they counted, retries and timeouts.
2. Look up the account, then get_usage for the request ids (or the invoice line). Read http_status, billed_tokens, cached_tokens, time (UTC) and billed_on_line. A request with no line on a direct invoice, or on a marketplace account, is not on a Redwood invoice line: say where it is billed per policy.
3. search_knowledge for the explanation, e.g. ["how billed tokens are counted tokenizer payload", "retries and streaming cancellation billing", "usage export columns filters timezone", "usage missing from console telemetry"]. Use current published pages; treat older pages as superseded when a current page says so.
4. Compare their count to the explanation: timezone used to cut the month vs UTC, project filters, prompt-only counting, client retries/timeouts, cancelled streams, tool-call payload, cache. Name the cause that fits their facts.
5. Decide:
   - Explained by a policy page: explain with their own numbers; no case, even if they call it a bug or double billing.
   - Still unexplained after those rules for specific request ids: route per the routing table (load billing-handoff) with the ids and what you checked.
   - Successful requests missing from the Console longer than the policy's wait: route per policy; shorter: tell them to wait and when to check again.
   - They want the correctly billed usage removed or credited: that's a credit/exception request; route per policy.
6. Reply with the answer first, then the detail and what they can change (e.g. retry settings), quoting console figures.
