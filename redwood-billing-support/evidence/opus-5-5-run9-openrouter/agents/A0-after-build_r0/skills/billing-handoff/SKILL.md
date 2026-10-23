---
name: billing-handoff
description: Use when a request is outside what Support can resolve in the conversation (goodwill or SLA credit asks, exceptions, payment failures, meter discrepancies, contract questions, missing usage) to pick the owning team and write a complete escalated case.
---
Goal: hand the case to the team current policy names, with everything they need, only when a handoff is actually required.

1. Is a case needed? search_knowledge for the routing policy, e.g. ["billing case routing owning team", "<the developer's ask in plain words>"]. If a current page explains what the developer sees (how tokens are counted, retries, cancelled streams, console lag, an older page superseded), explain it and stop: no case. If they only ask about typical amounts or contract figures, answer per the policy without a case.
2. Pick the team from the current routing table row that matches the situation, using console facts: plan (self-serve vs enterprise), contract on file and SLA flag, billing channel, invoice and request ids. If the developer claims a contract the console doesn't show, use the row for that mismatch. If two rows seem to fit, re-read their notes; delegate to policy-researcher if still unclear.
3. Collect intake. Note the details that row lists (e.g. invoice, lines, UTC window, request ids, decline code, time of failure, the ask and amount, why). Call ask_developer once for the missing ones. If they don't have some, proceed and record what's missing.
4. Tell them the rule that applies before routing when policy says to (e.g. a plan has no SLA, Support can't approve goodwill) and that the team decides.
5. Confirm. confirm_with_developer: "I'll hand this to <team> with <key details> so they can <what they do>." If no, don't escalate; tell them what they can do instead.
6. Escalate. Do any dispute or other write first. escalate_case with team, a summary (what they ask, on what, why this team) and details: account, invoice_id, line_ids, request_ids, amounts as the console shows them, UTC window, codes, plan/contract facts, what they checked, missing items. Never put card numbers in a case.
7. Reply in the same turn: which team has it, what that team does next per the policy, and what (if anything) the developer should prepare. Promise no outcome, amount or timeline.
