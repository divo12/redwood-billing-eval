---
name: account-terms-and-credits
description: Use for plan and pricing questions, free tier limits, credit balances and expiry, promo or workshop codes, contracts and SLA entitlement, marketplace or reseller billing, payment failures, card or autopay changes, overdue invoices and dunning, reservations, MDF, tax forms and vendor setup.
---
Goal: answer what applies to this developer's account from the console and current policy, and route only what Support can't resolve.

1. Look up the account (ask_developer ["org_id"] if needed). Note plan, plan_name, billing_channel, payment_method, status, billing_timezone, contracts (sla flag, dates).
2. search_knowledge with the topic and the account facts, e.g. ["credit ledger balance expiry", "SLA credit self-serve contract", "marketplace refund credit invoice support handling", "payment failures during payment-processor incidents", "payment card data handling", "dunning schedule 2027", "free tier quota error"]. Prefer the current published/final page; check Changes sections and effective dates against today.
3. Apply by case:
   - Balances: get_credit_balance. It reads the console's balance source of truth; quote each unexpired grant's balance and its own expiry date, plus the total. Don't quote a frozen or superseded table. Cash-out, refund, restoring an expired credit or other exceptions: explain, promise nothing, route per policy.
   - Contract or SLA: the console's contract and sla flag tell you which policy row applies. Don't recite contract terms or figures (availability %, rates, discounts); say a contract is on file and the account team confirms what it covers. An SLA credit claim goes where the routing policy says.
   - Marketplace (AWS/GCP) or reseller billing: the marketplace or reseller invoices them; Support issues no credit note, dispute or Redwood-branded invoice. Point them to the marketplace's own process; a case only where policy says (e.g. a Redwood vs marketplace metering mismatch).
   - Payment failure: ask for invoice, decline code and time (UTC) if missing. Search the payment-processor incident notices and check whether the failure time falls inside a published window (inclusive). Inside: name the incident, say the payment failed because of it, that it is retried automatically and WHEN the retries complete, that no late fees apply, that they need do nothing; no case. For the date: if this incident's notice gives the last-retry date, quote it. If not, search the earlier payment-processor notices ("Card payments failing last retries completed"), note for each how many days after its window ended the last retries completed, and apply that usual gap to this window's end; say it is based on the processor's usual schedule. Outside, or failed again after retries, or charged twice: route per policy.
   - Card or autopay change: never ask for, repeat or store any part of a card number; point them to Console billing settings; no case. If they share one, tell them not to send card details.
   - Plans/pricing/reservations/MDF/workshop codes: answer from published pages; label in-review or draft pricing as not final; say who handles it per the page.
4. If an action or team is needed, confirm credits/disputes with confirm_with_developer, then act or load billing-handoff.
5. Reply with the answer first, the console facts it rests on, and the next step.
