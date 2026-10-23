---
name: duplicate-charge-resolution
description: Use when a developer says an invoice charged them twice, has a repeated or duplicate line, or asks for a refund/credit of a charge they say was billed in error.
---
Goal: decide from the console and the current policy whether Support credits the copy line, opens a dispute and hands off, or hands off without a dispute — then do exactly that, once.

1. Collect identifiers. If the developer hasn't given them, call `ask_developer` for `org_id`, `invoice_id` (or `invoice_month`), `line_items`/`amounts` and `request_ids` in one call.
2. Read the console.
   - `lookup_account`: note `billing_channel`, `plan`, `contracts`, `billing_timezone`. If the account isn't billed directly by Redwood (marketplace or reseller), stop: search how that channel's bills are handled, explain it, route only if the policy says so.
   - `get_invoice`: read `duplicate_analysis` (original line = the one the usage records point to; copy line = the other), each line's amount and `already_credited`, existing credit notes. If you only have a month, pick the invoice whose period covers it.
   - `get_account_activity` for existing credit notes, disputes and cases.
3. Retrieve the rules with `search_knowledge`, and read the current published pages only:
   - the current support-console procedure for duplicate charges (the newest version that amends the routing page);
   - who may be credited at all (the current Support charter: account types, contract accounts);
   - Finance's approval-matrix limit for this invoice, and how to tell which price book rated it (Finance's close calendar);
   Ignore archived pages, drafts, proposals, Slack opinions and limits that belong to other teams or channels.
4. Classify from the console only (a matching description or amount is not proof):
   - Confirmed duplicate: two lines on the same direct invoice bill the same request ids AND the usage records point to exactly one of them (`console_confirms_duplicate: true`). Act only on the copy line.
   - Not confirmed: different request ids, one line, or usage pointing to neither/both lines.
   Don't tell the developer a credit is likely before this step confirms the duplicate.
5. Act according to the retrieved procedure:
   - Confirmed, the account is one Support may credit, and the copy line's full amount is within the limit for this invoice → `confirm_with_developer` naming the invoice, the copy line id and the exact amount → `issue_duplicate_credit` for the copy line's full amount (dollars, e.g. "60.00"). Never split a line.
   - Confirmed, but Support may not credit this account (e.g. a signed contract) or the line is over the limit → `confirm_with_developer` that you will open a dispute on the copy line and pass it to the team the procedure names → `open_dispute` (reason `duplicate_charge`, copy line only, the request ids, window) → `escalate_case` to that team with invoice, original and copy line ids, request ids, window, the ask and the dispute id.
   - Not confirmed → do not credit. Explain what the console shows (e.g. the lines bill different request ids, with their times), then do what the procedure says for unconfirmed duplicates (collect intake, hand off). A handoff with no credit or dispute needs no confirmation.
   - Already credited or disputed → say so; do nothing twice. If a credit "didn't arrive", check the credit notes first and explain when credits apply (per the procedure / close calendar).
   - If the developer says no: write nothing for it (no credit, dispute or case); tell them nothing changed and they can come back.
6. Windows in a handoff or dispute are in the account's billing timezone: use `at_billing_timezone` from `get_usage`, or the developer's answer converted.
7. Reply: what the console showed, what you did (credit note id and amount, or dispute id and the team that has the case), when a credit shows up if the procedure says so. Don't promise a dispute outcome or a date.
