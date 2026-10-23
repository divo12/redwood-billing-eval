---
name: billing-handoff
description: Use when a request needs another Redwood team (Billing Ops, Billing Engineering, Finance, account team, Partnerships, Telemetry, etc.) or you are unsure whether a case is needed at all. Gives the steps to decide, collect intake, escalate once and tell the developer.
---
1. Decide whether a case is needed. Search the routing policy (`search_knowledge`, e.g. "billing case routing <topic>"). If a current page explains what the developer sees, or they only asked a question (how something is billed, what their contract says, typical amounts), answer it and stop: no case. Open a case only when they ask for something Support can't do in the conversation, or report a problem the pages don't explain.
2. Pick the owning team from the current routing passage for this exact situation. Watch for the distinguishing condition in each row: billing channel (direct vs marketplace/reseller), plan (self-serve vs contract), whether the charge is an error or correct, whether the console confirms the problem. If the developer claims something the console doesn't show (e.g. a contract), follow the policy row for that.
3. Collect the intake that row lists. Use what the developer already wrote and the console; call `ask_developer` once for the rest (e.g. invoice_id, line_items, request_ids, time_of_issue + timezone, error_code, checks_done, contract). If they don't have something, proceed and record it as missing.
4. Answer everything you can first: escalation closes the console for the rest of the conversation.
5. `confirm_with_developer`: "I'll hand this to <team> with <key facts>." If they decline, don't escalate; tell them how to come back.
6. `escalate_case` once: team, a summary (what they asked, what the console shows, what policy says, what you already did), details (account_id, invoice_id, line_ids, request_ids, amounts, UTC window, error code, plan/contract, missing items, policy source ids), and the dispute id if any.
7. Tell the developer the team that has it, the case id, what that team does next per policy, and that you can't promise an outcome, amount or date.
