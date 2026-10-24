# Payment questions and invoice requests

id: "POL-BIL-01"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Billing"
related_policy_ids: ["POL-BIL-02", "POL-RET-06", "POL-SEC-02", "POL-FRD-01", "POL-SUP-01"]

## S1 Scope

Questions about recorded charges, available payment methods, payment failures, and invoices.

## S2 Conditions

Match payments.order_id to an owned order. Use amount_cents, status, method, and captured_at to explain charges.
The fictional checkout accepts Visa, Mastercard, American Express, and Discover credit or debit cards. These are proposed simulation terms, not a description of a live service.

## S3 Policy rules

Explain a verified charge using its date, amount, and related order. The fixture status captured means a completed capture; a customer report of a declined attempt does not alter that ledger record.
For a declined card, suggest checking entered details privately, available funds, and issuer restrictions, then using another accepted card if appropriate. Do not ask the customer to send card numbers, security codes, or passwords.
A payment summary is not an official invoice. No invoice document, invoice link, or invoice-creation tool exists in this store snapshot. If the customer needs an invoice, hand the request to Human Review for Billing staff.
For missing order details, ask the customer. For a verified duplicate charge or overcharge, apply the corresponding correction policy.

## S4 Exceptions

Never claim that an invoice was generated, attached, or sent without a supporting receipt. Ticket-quoted invoice numbers are unverified.
An unrecognized charge, suspected fraud, or a continuing payment failure requires Human Review.
