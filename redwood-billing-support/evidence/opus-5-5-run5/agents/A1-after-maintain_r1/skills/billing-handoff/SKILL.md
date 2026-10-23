---
name: billing-handoff
description: Use when a request may be outside what Support can do in the conversation (goodwill or SLA credit, refund, exception, contract or discount question, payment failure, card change, meter discrepancy, missing usage, marketplace or reseller bill, reservation change) and you must decide whether and where to hand it off.
---
Goal: route the case to the team current policy names, with everything that team needs — or answer without a case when the current pages say no case is needed.

1. Identify the request type in one line (e.g. "SLA credit claim, enterprise account", "card payment declined", "usage missing after 40 minutes").
2. `search_knowledge` for the current routing/handoff rules for that type and any page specific to it (payment failures and processor incidents, SLA, marketplace/reseller billing, credit ledger, usage delays, error reference, Support charter). Use the newest published page; a page's change log tells you what changed and from when. Drafts, proposals and Slack opinions don't change the rules.
3. Check the account: `lookup_account` → plan, billing channel, contracts, billing timezone, status. The console decides which routing row applies.
4. Check whether a published page explains it without a case, for example:
   - a card payment that failed inside a published payment-processor incident window (compare the failure time in UTC with the incident notice's window; the time decides, not the decline code) → explain the incident, the automatic retries and when they complete, no late fee, nothing to do; no case;
   - usage still within the documented delay for that product/time → explain and say when to check again;
   - balance/expiry questions → answer from `get_credit_balance` (the ledger), no case;
   - card changes → the customer does it in Console billing settings; never take card details; no case;
   - billing behaviour the pages explain (token counting, retries, quota vs rate-limit errors) → answer; no case.
5. If a team is needed, gather the intake the routing page lists with ONE `ask_developer` call for the missing fields. If the developer doesn't have some, proceed and record them as missing.
6. Answer every question the developer asked first. State general rules without quoting contract figures or internal-only ranges.
7. A handoff that writes no credit or dispute needs no confirmation under current standards; if the developer already declined an action, write nothing. Call `escalate_case` once with `team`, `summary` (ask + what you found), `details` (all intake facts; windows in the account's billing timezone; the amount asked if any; missing items; any dispute id) and `priority` `normal` unless the current routing page calls for `urgent`. This closes the console, so do it last.
8. Reply: which team has it, what they will review, and that you can't promise an outcome, amount or date.
