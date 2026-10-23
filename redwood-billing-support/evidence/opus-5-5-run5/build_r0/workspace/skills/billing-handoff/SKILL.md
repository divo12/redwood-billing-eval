---
name: billing-handoff
description: Use when a request is outside what Support can do in the conversation (goodwill or SLA credit, exception, waiver, contract or discount question, payment failure, card change, meter discrepancy, missing usage, marketplace or reseller bill) and you must decide whether and where to hand it off.
---
Goal: route the case to the team current policy names, with everything that team needs, or answer without a case when policy says no case is needed.

1. Identify the request type in one line (e.g. "SLA credit claim, enterprise account", "goodwill credit after correct retry charges", "card payment declined").
2. `search_knowledge` for the billing case routing / handoff rules for that type, plus any page specific to it (payments, SLA, marketplace or reseller billing, credit balances, usage accounting). Prefer the current published routing page; note any later published page that amends it for this type.
3. Check the account first: `lookup_account` → plan, billing channel, contracts (and `sla` / signed terms), status. The console decides which routing row applies (e.g. self-serve vs contract, direct vs marketplace). If the developer claims a contract the console doesn't show, use the routing rule for that situation.
4. Decide:
   - The documents answer the question (how tokens are counted, why retries or cancelled streams are billed, console lag, a credit balance, a general rule) → answer it; no case.
   - The request needs a team → note the team and the intake details the routing page lists for it.
   - The channel is billed by someone else (marketplace, reseller) → tell the developer who adjusts or bills them and what to do; route only if the policy says Support routes it.
5. Gather intake with ONE `ask_developer` call for the missing fields (e.g. `invoice_id`, `line_items`, `amounts`, `request_ids`, `time_of_issue`, `timezone`, `error_code`, `checks_done`, `contract`). If the developer doesn't have some, proceed and record them as missing.
6. Answer every question the developer asked before the handoff. State general rules that apply (e.g. whether their plan carries an SLA) without quoting internal-only figures or ranges.
7. `confirm_with_developer`: "I'll hand this to <team> with <key facts>." On "yes", call `escalate_case` once with `team`, a `summary` (ask + what you found) and `details` (all intake facts, the amount asked if any, the reason, missing items, any dispute id). This closes the console, so do it last.
8. Reply: which team has it, what they will review, and that you can't promise an outcome, amount or date.
