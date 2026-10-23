---
name: account-terms-and-credits
description: Use for plan and pricing questions, free tier limits, credit balances and expiry, contracts and SLA entitlement, marketplace or reseller billing, payment failures, card or autopay changes, overdue invoices, tax forms and vendor setup.
---
Goal: answer what applies to this developer's account from the console and current policy, and route only what Support can't resolve.

1. Look up the account (ask_developer ["org_id"] if needed). Note plan, plan_name, billing_channel, payment_method, status, contracts (sla flag, terms, dates).
2. search_knowledge with the topic and the account facts, e.g. ["free tier monthly limit 429", "credit balance grants expiry", "SLA credit self-serve contract", "marketplace billed invoice adjustments", "reseller invoice disputes", "failed card payment", "dunning overdue suspension", "W-9 vendor payment"]. Prefer current published/final pages; check status cards or incident notes for the dates involved.
3. Apply by case:
   - Balances: get_credit_balance; quote the total and each grant's remaining amount and expiry (no expiry = doesn't expire). Expired grants don't count.
   - Contract or SLA: the console's contract record and sla flag tell you which policy row applies. Check what the policy lets you disclose: do not recite the contract's terms or figures (availability %, rates, discounts) to the developer, even though the console shows them; say that a signed contract with an SLA is on file and that the account team confirms what it covers. An SLA credit claim goes where the routing policy says for this plan/contract; no amount is promised.
   - Marketplace or reseller billing: the marketplace or reseller invoices them; Support issues no credit note or dispute. Tell them who to contact per policy.
   - Payment failure: ask for invoice, decline code and time if missing. If an incident window covers it, say what the incident note says happened (retries, fees). Otherwise route per the payment policy. Never ask for or repeat card numbers.
   - Plan/pricing questions: answer from published pricing pages; label in-review or draft pricing as not final.
4. If an action or team is needed, confirm_with_developer, then act or load billing-handoff.
5. Reply with the answer first, the console facts it rests on, and the next step.
