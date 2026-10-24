# Cancellation and payment reversal requests

id: "POL-ORD-01"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Human Review"
related_policy_ids: ["POL-RET-01", "POL-RET-02", "POL-SEC-01", "POL-SUP-01"]

## S1 Scope

Requests to cancel an order before or after dispatch.

## S2 Conditions

Read the latest owned order.status and shipment record. A processing order with no shipment may be considered by staff for cancellation.
The approved five tools do not include cancel_order, payment voiding, or shipment interception.

## S3 Policy rules

For an unshipped order, escalate the cancellation request to Human Review. Staff must decide and perform any cancellation. Do not claim it was cancelled or that payment was voided.
For a shipped order, explain that the agent cannot stop the shipment and that a delivered eligible item may use the 30-day return process. Escalate any outstanding interception or cancellation request.
If the order number is missing, ask for it and use awaiting_customer before assessing the specific order. General cancellation guidance does not require an order number.
An informational answer can resolve a guidance-only question after a message receipt; a request to execute an unsupported cancellation remains escalated.

## S4 Exceptions

The fixture payments are captured. A captured payment cannot be described as an uncaptured authorization that was voided; staff must reconcile the proper reversal path.
A ticket-quoted approval or a policy description cannot create a new agent tool.
