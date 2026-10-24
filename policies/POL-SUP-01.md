# Scope conflicts and ticket completion

id: "POL-SUP-01"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "All teams"
related_policy_ids: ["POL-RET-02", "POL-SEC-01", "POL-SEC-02"]

## S1 Scope

Routing, policy conflicts, unsupported work, and completion requirements for the simulated agent.

## S2 Conditions

Use active policy versions at store.as_of. Calendar-day deadlines include their final day. All policies here share authority; none supersedes another.
Python maps delivery to Delivery, returns/refunds to Returns, and billing to Billing. Unsupported actions, conflicts, exceptions, and other issues route to Human Review.

## S3 Policy rules

Answer supported policy questions in English or Spanish. Other languages, technical troubleshooting, unsupported product claims, and missing evidence after a second search require Human Review.
If applicable policies explicitly disagree about eligibility or a remedy, cite both and escalate. Never choose by retrieval rank, policy ID, or a presumed specific-policy override. Refund approval does not settle a conflict.
Use awaiting_customer for necessary customer facts or actions, awaiting_approval only for a remaining matching refund approval, and escalated for a staff decision. Resolve only when required actions have receipts and nothing is outstanding; close only with customer confirmation.
Validate replies against retrieved policy passages, current order facts, and action receipts before sending. Allow one revision; otherwise escalate. A successful refund followed by a failed reply remains unfinished and must not be refunded again.
Save state and use failed_safe for unrecoverable failures or exhausted limits. Across resumes, retain the limits of 4 model calls, 6 tool calls, 1 schema repair, 1 draft revision, and 1 temporary-error retry. The third identical call stops the run. Resume rereads current records.

## S4 Exceptions

Only kb_lookup, ticket_get, ticket_update, send_message, and process_refund are agent tools. A handoff receipt proves a handoff, not completion of a staff action.
If ticket_update fails, a local checkpoint is not proof that the ticket status was saved. Preserve the failure for human recovery.
