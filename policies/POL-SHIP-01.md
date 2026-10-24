# Order tracking and delivery updates

id: "POL-SHIP-01"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Delivery"
related_policy_ids: ["POL-SHIP-03", "POL-SEC-02", "POL-SUP-01"]

## S1 Scope

Information about an existing customer order and its recorded shipment.

## S2 Conditions

Match the ticket customer_id to orders.customer_id before using order details. Read shipment.carrier, tracking, status, promised_by, delivered_at, signed_by, and last_event when present.
Use store.as_of as the simulation date. A null shipment or null last_event does not prove loss.

## S3 Policy rules

Share the recorded carrier, shipment status, latest available scan, and promised date. Describe promised_by as the recorded estimate, not a guaranteed arrival.
If the order number is missing or several owned orders could match, ask which order the customer means and use awaiting_customer. General shipping questions do not require an order number.
For a status-only request, a supported reply with an outbox receipt can resolve the ticket. Refer late, lost, and delivered-but-not-received cases to POL-SHIP-03.
Do not claim to contact the carrier, change a shipment, or open a trace. None of those operations exists in the approved tool set.

## S4 Exceptions

An order belonging to another customer follows POL-SEC-02. An unknown ticket ID stops the run under the tool contract.
Conflicting shipment facts require Human Review; never fill a missing scan or date with an estimate.
