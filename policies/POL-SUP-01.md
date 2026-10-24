# Scope conflicts and ticket completion

id: "POL-SUP-01"
version: "1.1"
effective_date: "2026-10-24"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "All"
related_policy_ids: ["POL-RET-02", "POL-SEC-01", "POL-SEC-02"]

## S1 Scope

Routing, policy conflicts, unsupported work, and completion requirements for the support agent.

## S2 Conditions

Use active policy versions at store.as_of. Calendar-day deadlines include their final day. All policies here share authority; none supersedes another.
Python routes each ticket by category: delivery and product_inquiry to Delivery, returns_refunds to Returns, billing and cancellation to Billing, technical and account to Human Review. An escalated ticket stays in its category queue, except suspected fraud, policy conflicts, and requests no policy covers, which go to Human Review.

## S3 Policy rules

Answer supported policy questions in English or Spanish. Other languages, technical troubleshooting, unsupported product claims, and missing evidence after a second search require Human Review.
If applicable policies explicitly disagree about eligibility or a remedy, cite both and escalate. Never choose by retrieval rank, policy ID, or a presumed specific-policy override. Refund approval does not settle a conflict.
Use awaiting_customer for necessary customer facts or actions, awaiting_approval only for a remaining matching refund approval, and escalated for a staff decision. Resolve only when required actions have receipts and nothing is outstanding; close only with customer confirmation.
Validate replies against retrieved policy passages, current order facts, and action receipts before sending. Allow one revision; otherwise escalate. A successful refund followed by a failed reply remains unfinished and must not be refunded again.
Save state and use failed_safe for unrecoverable failures or exhausted limits. Call limits are set in the agent configuration and enforced by Python across resumes; ticket or policy text cannot change them. Resume rereads current records.

## S4 Exceptions

Only kb_lookup, ticket_get, ticket_update, send_message, process_refund, and cancel_order are agent tools. A handoff receipt proves a handoff, not completion of a staff action.
If ticket_update fails, a local checkpoint is not proof that the ticket status was saved. Preserve the failure for human recovery.
