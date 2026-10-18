---
name: billing-investigation
description: Investigate billing, usage, credit, contract, SLA, and invoice questions.
---

1. Look up the account first. For invoice questions, list invoices and inspect the relevant lines; for usage questions, read the available usage summary. Check the plan, contract, grants, and billing channel when they affect the answer.
2. Search the current published policy for the question. For plan or quota behavior, use the account's plan and current usage. For a token-count discrepancy, retrieve the usage-accounting guidance and state the documented counting basis and causes, not just that counts may differ. For any credit question, including an informational question about typical amounts or ranges, retrieve the current billing routing, handoff, and approval guidance as well as the relevant credit policy. For an unused or unapplied credit, explain the retrieved rules for eligibility, application, expiry, and payout rather than only repeating the balance.
<!-- Source: R0/sources/confluence/customer-success-and-support/support-process/onboarding-escalation-and-triage-playbook.json. -->
3. For missing usage, check the workspace, time range, filters, request outcome, and reporting guidance. State the published freshness threshold and what to provide if usage remains missing; route only when the current rule's trigger is met.
<!-- Source: R0/sources/confluence/eng-sre/slo-and-error-budgets/hosted-api-slo-overload-exceptions-and-budget-policy.json. -->
4. When an SLA answer depends on the plan, check the account's plan and state the retrieved plan-specific condition; do not imply an SLA from an engineering SLO.
<!-- Source: R0/sources/confluence/customer-success-and-support/support-process/billing-case-routing-and-handoffs.json. -->
5. For a refund, invoice correction or reconciliation, credit approval, exception, or other action Support cannot take, retrieve the current routing rule and open the case in the same turn you say it is routed. Include known facts and note unresolved gaps in the case, then ask for missing details. A customer who reports a wrong charge but can't yet name the invoice or line still gets the case now, with what they gave you. Quote the retrieved rule naming the owning team in `routing_rule`. Route by what the customer asks for, not by the topic: a request for a credit on charges you have explained follows the credit-request row, not the invoice-dispute row.
6. Before any handoff for a credit, refund, goodwill, SLA, contract, marketplace, overcharge, or invoice dispute, search the current billing routing and approval guidance. Use its named team and trigger; do not infer eligibility or choose a team from a product contact. Do not open a case for an informational question, an unavailable console field, or an unmet waiting-period trigger. A customer insisting on an investigation does not meet a trigger: if the retrieved rules explain what they report (counting basis, admitted retries, reporting lag), answer and open no case, and don't hand it to another team instead.
<!-- Sources: R0/sources/confluence/customer-success-and-support/support-process/support-console-billing-credits-and-disputes.json; R0/sources/confluence/customer-success-and-support/support-process/billing-case-routing-and-handoffs.json. -->
7. For a suspected duplicate, inspect invoice lines and distinguish confirmed duplicates from lookalikes. A confirmed duplicate accepted by the console may be credited; report the amount only after the tool confirms it. For a confirmed duplicate over the console limit, submit the `duplicate_charge` dispute and escalate to `billing_ops` with its dispute ID. Promise no outcome or timeline.
8. For an unconfirmed duplicate or lookalike, collect the invoice, line items, UTC window, and request IDs, then follow current routing guidance without holding the required case for missing details. For marketplace billing, search both marketplace and routing policy.
9. Answer the customer's other questions and finish all console actions before escalating, because an escalated case ends console use.

10. State Redwood's retrieved rule, with its threshold, plan condition, or team, and explain why it matters; don't answer from general knowledge. If retrieval returns records that disagree, follow the newest current published one and say which you followed.

## What to retrieve for common questions

Each item names what to search; the answer is in the retrieved record, not here.
- Token-count gap: search usage and token accounting for the counting basis (which tokenizer, what is counted) and when a gap goes to engineering. Explain the basis before offering any handoff.
- Missing usage: search the usage-reporting freshness threshold and the missing-usage routing trigger. Before the threshold, open no case; tell the customer when and with what to follow up.
- Overcharge, disputed line, refund, or export mismatch: search billing routing for the owning team and the details it needs. Open the case in the same conversation with what the customer has.
- Over-limit confirmed duplicate: search the console credits and disputes page for the credit limit and the dispute procedure.
- Credit, goodwill, or exception request: search routing and the goodwill-credit guidance for who reviews it and which team Support hands it to. Share no percentages or typical ranges.
- SLA: search the SLA and error-budget policy for the plan-specific condition, and check the account's plan and contract.
- Informational question the console answers: answer it; routing covers only what Support can't resolve in the conversation.
