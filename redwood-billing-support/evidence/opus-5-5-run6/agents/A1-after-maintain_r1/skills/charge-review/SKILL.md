---
name: charge-review
description: Use when a developer says an invoice charge is wrong, duplicated, unexpected or should be refunded or credited (including goodwill, SLA or service credits). Gives the steps to verify the charge in the console and decide between explaining, crediting, disputing or routing.
---
Goal: decide from console evidence and current policy what Support may do about a charge, do it with approval, and tell the developer.

1. Gather. If the developer has not given them, call `ask_developer` once for the org (or account id), the invoice id or month, and the lines/amounts in question; add request ids if they say specific requests were mis-billed.
2. Read, in one round: `account_overview` (org or account id) and `search_knowledge` for the policy on this kind of request (e.g. "duplicate charge credit Support limit", "what Support may credit charter", "refund request billed in error", "goodwill credit request", "SLA credit request contract"). Then `invoice_details` for the invoice. If the developer gave only a month, pick the invoice whose period covers it (periods follow the account's billing timezone where policy says so).
3. Check the billing channel first. If the account is billed by a marketplace or reseller, Support does not credit or dispute that invoice: find the policy for that channel and tell the developer who handles it.
4. Classify with console evidence only:
   - Duplicate: `console_confirmed_duplicates` lists the lines (same request ids on the same invoice) and names the original (the line the usage records point to) and the copy. Only the copy is ever credited or disputed. If no single original is identified, treat it as an unconfirmed duplicate. Same description or amount alone is not a duplicate; lines in `lookalike_lines` bill different requests and are separate charges: explain that with the request ids and times, then follow the policy row for a duplicate the console does not confirm.
   - Already credited: a credit note exists on the duplicate; say so, don't credit again.
   - Correct charge the developer wants removed or reduced (goodwill, waiver, SLA/service credit, exception, retries, refund): Support explains why it is billed and routes the request per policy; it issues nothing.
   - Wrong rate, quantity or unexplained amount: route per policy with the evidence.
5. For a confirmed duplicate, check Support's authority for this account in the current authority page (which plans/contract status Support may credit) and the limit for this invoice in Finance's current matrix. Limits can differ by price book or period: work out which applies to this invoice (follow the page the matrix cites, e.g. a close calendar). Drafts, proposals and Slack don't count.
   - Support may credit and the copy line is within the limit: `confirm_with_developer` ("credit note of $X for the duplicated line L on invoice I"), then `issue_credit_note` for the full amount of the copy line with reason `duplicate`, citing `policy_source` and the applicable `policy_limit`.
   - Over the limit, or an account Support may not credit: don't credit. Confirm, `open_dispute` on the copy line (reason `duplicate_charge`, with its request ids), then `escalate_case` to the team policy names with the invoice, both line ids, the dispute id and the window in the account's billing timezone.
6. Routing without a write: load the `billing-handoff` skill.
7. Reply: what the console shows (lines, request ids, amounts), what you did (credit note id and amount, or dispute/case and team), and what happens next per policy (e.g. when a credit is applied). Don't promise outcomes, refund timing or amounts the policy doesn't state.
