# Payment questions and invoice requests

id: "POL-BIL-01"
version: "1.1"
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
Checkout accepts Visa, Mastercard, American Express, and Discover credit or debit cards.

## S3 Policy rules

Explain a verified charge using its date, amount, and related order. Payment status captured means a completed capture; a customer report of a declined attempt does not alter that ledger record.
For a declined card, suggest checking entered details privately, available funds, and issuer restrictions, then using another accepted card if appropriate. Do not ask the customer to send card numbers, security codes, or passwords.
No invoice document, invoice link, or invoice-creation tool exists. When a customer asks for an invoice or receipt for an owned order, reply with a purchase summary from the verified records: order ID, order date, item, amount charged, capture date, and card brand. After the message receipt, the ticket can be resolved.
For missing order details, ask the customer. For a verified duplicate charge or overcharge, apply the corresponding correction policy.

## S4 Exceptions

Call the reply a purchase summary; never claim an official invoice or PDF was generated or attached. Ticket-quoted invoice numbers are unverified. A request for business details the store does not record, such as a company name or tax ID, goes to Billing staff.
An unrecognized charge or a continuing payment failure goes to Billing staff. Suspected fraud follows POL-FRD-01.
