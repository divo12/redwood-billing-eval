---
name: usage-reconciliation
description: Use when a developer's own token or request counts, usage export, or Console numbers don't match what was billed, usage is missing or late, or they ask where a request is billed or why a failed, retried, cancelled or cached request was charged.
---
1. Collect: org, the request ids, their HTTP status, when it happened and the timezone they used, and how they counted (`checks_done`: filters, tokenizer, retries, timeouts). Ask in one `ask_developer` call for what's missing.
2. Read in one round: `account_overview`, `usage_lookup` for the request ids, and `search_knowledge` for how billed tokens are counted and how retries, cancellation, caching, console delay and exports work.
3. Compare console facts with what they report: UTC time vs their timezone, `http_status`, `billed_tokens`, `cached_tokens`, and `line_id`/`invoice_id` (null = not on a Redwood invoice line yet, or billed elsewhere, e.g. by a marketplace; check the account's billing channel and the policy for that channel).
4. If a current page explains the difference (counting method, retries, cancellations, timezone or filter cut, delay), explain it with their numbers. No case.
5. If the difference remains after those rules, or successful usage is still missing after the delay policy states, route it with `billing-handoff` to the team the routing passage names, with request ids, statuses, UTC window and what was checked.
