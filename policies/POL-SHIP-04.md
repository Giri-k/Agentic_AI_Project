# Delivery address changes

id: "POL-SHIP-04"
version: "1.1"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Delivery"
related_policy_ids: ["POL-SEC-02", "POL-SUP-01"]

## S1 Scope

Requests to change an address or redirect an existing shipment.

## S2 Conditions

Verify customer ownership and the latest order.status and shipment record.
An unshipped order has no dispatch record; shipment=null alone must be checked with order.status.

## S3 Policy rules

For an unshipped order, hand the request to Delivery staff. Tell the customer that the address change is requested and remains unconfirmed.
For a shipped order, explain that staff must assess any carrier redirection option; do not promise interception or successful delivery to a new address.
The agent cannot modify an address, update a carrier record, or send another customer details. It may ask for necessary delivery information through the customer-scoped channel.

## S4 Exceptions

Conflicting ownership, another person requesting redirection, or suspicious fund or parcel redirection requires security review.
A customer typing a new address does not establish that it has been saved. Never describe the address as changed without an authorized staff record.
