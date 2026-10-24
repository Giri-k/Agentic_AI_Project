# Partial refunds and billing corrections

id: "POL-RET-06"
version: "1.1"
effective_date: "2026-10-24"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Returns"
related_policy_ids: ["POL-RET-02", "POL-RET-03", "POL-BIL-02", "POL-BIL-03", "POL-SUP-01"]

## S1 Scope

Calculating a refund after a policy establishes entitlement, including an incorrect billed amount.

## S2 Conditions

Use integer cents in USD. Read orders.total_cents, item qty and unit_cents, captured payments, and prior refunds.
Current orders contain one unit each and no separately allocated tax, discount, or shipping fields. Do not invent additional monetary components.

## S3 Policy rules

Remaining refundable balance equals captured payment cents minus refunds already issued or settled. Apply that limit across all requests for the order.
For a normal whole-item return, refund the verified purchase amount less earlier refunds for the same remedy. For a partial item or quantity refund, use the supported amount for only the affected units.
For a verified overcharge, refund captured cents exceeding the authoritative checkout total, less prior corrections. Do not refund the legitimate charge. Duplicate captures use POL-BIL-02.
An order paid at 13,999 cents with 4,199 cents already refunded has at most 9,800 cents remaining. An approval for 13,999 cents cannot be resized or reused for 9,800 cents; staff must reconcile it and provide an exact new approval.

## S4 Exceptions

Ambiguous prior-refund purpose, missing checkout evidence, non-USD amounts, or unallocated components require staff review.
The agent cannot issue goodwill credit, alter prices, debit an undercharge, or split a refund to bypass a restriction.
