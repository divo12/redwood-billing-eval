---
name: duplicate-charge-resolution
description: Use when a developer says an invoice charged them twice, has a repeated or duplicate line, or asks for a refund/credit of a charge they say was billed in error.
---
Goal: decide from the console and the current policy whether Support credits the line, opens a dispute and hands off, or hands off without a dispute — then do exactly that, once.

1. Collect identifiers. If the developer hasn't given them, call `ask_developer` for `org_id`, `invoice_id` (or `invoice_month`), `line_items`/`amounts` and `request_ids` in one call.
2. Read the console.
   - `lookup_account` (org or account id). Note `billing_channel` and `plan`. If the account is not billed directly by Redwood (marketplace or reseller), stop this procedure: search the knowledge base for how that channel's invoices are adjusted, explain it, and route only if the policy says so.
   - `get_invoice` for the invoice (if you only have a month, pick the invoice whose period covers it from `lookup_account`). Look at `lines_billing_identical_request_ids`, each line's amount and `already_credited`, and the credit notes.
   - `get_account_activity` to see existing credit notes, disputes and cases.
3. Retrieve the rules. `search_knowledge` for the current support-console procedure for duplicate charges and for Finance's approval limit on duplicate-charge credits (who issues it, the per-line limit, whether lines may be split). Read the published, current documents; ignore archived help articles, drafts and proposals, and limits that belong to other teams or channels.
4. Classify, using the console only (a matching description or amount is not proof):
   - Confirmed duplicate: two lines on the same direct invoice bill the same request ids.
   - Not confirmed: different request ids, only one line, or no request ids.
5. Act according to the retrieved procedure:
   - Confirmed and the duplicated line's full amount is within Support's limit → `confirm_with_developer` naming the invoice, the line id and the exact amount → `issue_duplicate_credit` for the repeated line's full amount. Never split a line into smaller credits.
   - Confirmed but over the limit → `confirm_with_developer` that you will open a dispute on the duplicated line and pass it to the owning team → `open_dispute` (reason `duplicate_charge`, the line, the request ids, submitted) → `escalate_case` to the team the procedure names with the invoice, line(s), request ids and the dispute id.
   - Not confirmed → do not credit. Explain what the console shows (e.g. the two lines bill different request ids, with their UTC times and statuses), then do what the retrieved procedure says for duplicates the console doesn't confirm (e.g. collect intake details and hand the case to the team it names). Answer "is it a duplicate?" plainly: not per the console.
   - Already credited or already disputed → say so; do nothing twice.
   - For any handoff, include the UTC window: use the developer's answer, or the `at` times of the request ids from `get_usage` when they don't know it.
6. Reply: what the console showed, what you did (credit note id and amount, or dispute id and the team that has the case), and what happens next. Don't promise a dispute outcome, a refund date or timing. If the developer asks about money already paid on a credited invoice, answer only from what the documents say.
