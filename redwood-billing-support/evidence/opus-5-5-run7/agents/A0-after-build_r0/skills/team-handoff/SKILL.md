---
name: team-handoff
description: Use before escalating any case to another Redwood team (billing ops, billing engineering, finance, account team, partnerships, telemetry, etc.). Picks the owning team from current routing policy and writes a complete case.
---
1. Decide whether a case is needed at all. search_knowledge for the routing policy covering this request (e.g. "billing case routing <topic>", "who approves <request>"). If a current page says Support answers this without a case (an explanation, a general rule, "your account team confirms your terms"), answer and stop.
2. Pick the owning team from the current routing/approval documents, not from old drafts or threads. Use the console account (plan, channel, contracts) to choose between rows, e.g. self-serve vs signed contract, direct vs marketplace/reseller. If the developer claims something the console doesn't show (a contract, a plan), route per the policy for that situation.
3. Collect the intake the receiving team needs (the routing page lists it per row) with one ask_developer call: e.g. invoice_id, line_items, amounts, request_ids, time_of_issue together with timezone, error_code, checks_done, contract. If they don't have something, hand off anyway and record it as "not provided" (not as a negative answer).
4. Do any permitted write first (credit note or dispute), because escalation closes the console.
5. confirm_with_developer: "Hand this to <team> with <one-line summary>." If no, don't escalate; tell them what they can do instead.
6. escalate_case with: team; summary (what they asked, what the console shows, what the team must decide/do); details with every fact given (ids, amounts as the console shows them, UTC window, request ids, the amount requested if any, what's missing); dispute_id if any. Never include card numbers.
7. Tell the developer: anything the policy row says to tell the customer (e.g. a rule that applies to their plan or channel), which team has it, the case id, and what that team will do next. No promised outcome, amount or date; no internal credit tables or ranges.
