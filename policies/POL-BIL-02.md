# Duplicate captured payments

id: "POL-BIL-02"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Billing"
related_policy_ids: ["POL-RET-02", "POL-RET-03", "POL-RET-06", "POL-SEC-02", "POL-SUP-01"]

## S1 Scope

Reports that a customer was charged more than once for the same order.

## S2 Conditions

Two or more payment records are captured against the same owned order, and their total exceeds the verified order total.
Verify that the excess is not a legitimate split payment or a correction already refunded. A pending authorization is not a second capture.

## S3 Policy rules

The refundable duplicate amount is the verified excess captured amount after prior duplicate-charge corrections. Keep the legitimate order payment intact.
For two captures of 5,999 cents on a 5,999-cent order with no prior correction, the eligible correction is 5,999 cents. It requires exact Billing staff approval under POL-RET-02.
If the duplicate is verified and approval is the only missing requirement, use awaiting_approval. A successful refund receipt is required before saying the duplicate was refunded.
The store currently records captures only. If later data adds pending authorizations, explain their recorded state without refunding an uncaptured amount or inventing a release date.

## S4 Exceptions

Unclear allocation across captures, a contested order total, another customer payment, or an open dispute requires Human Review.
If the tool cannot associate a correction with the appropriate captured funds, pause execution for staff reconciliation.
