# Refund timing status and confirmation

id: "POL-RET-03"
version: "1.1"
effective_date: "2026-10-24"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Returns"
related_policy_ids: ["POL-RET-02", "POL-RET-06", "POL-SEC-02", "POL-SUP-01"]

## S1 Scope

Customer questions about refunds already recorded in the refund ledger.

## S2 Conditions

Read refunds.refund_id, order_id, amount_cents, created_at, status, and the associated approval and customer ownership.
An approval without a successful refund ledger record does not mean money was issued.

## S3 Policy rules

Issued refunds normally settle in 5 to 10 business days from created_at. Count the next Monday through Friday date as business day 1; weekends are excluded and holidays are not modeled.
Refunds go to the original payment method. Do not redirect funds to a new account or person. State the recorded amount, issue date, and receipt ID when available.
Status issued means issued, not received. Status settled means the ledger records settlement; a separate customer confirmation is required before closing the ticket.
A refund-status inquiry can be resolved with a supported reply when no further service action is needed. A resolved ticket can become closed once the customer confirms receipt. Never refund again merely because the customer asks for an update.

## S4 Exceptions

If the customer reports nonreceipt after more than 10 business days, escalate to Returns staff for a ledger investigation.
A missing receipt, inconsistent ledger, or failed message must not be described as successful completion.
