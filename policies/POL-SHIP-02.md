# Shipping destinations speeds and fees

id: "POL-SHIP-02"
version: "1.1"
effective_date: "2026-10-24"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Delivery"
related_policy_ids: ["POL-SHIP-01", "POL-SUP-01", "POL-RET-06"]

## S1 Scope

General shipping information for the fictional marketplace.

## S2 Conditions

The shipping destination is within the United States. Published durations are business days after dispatch, not after order placement.
Business days are Monday through Friday; weekends are excluded and holidays are not modeled.

## S3 Policy rules

The marketplace ships to all 50 states. Standard shipping to the contiguous 48 states is 3 to 5 business days and costs $4.99 per order. Express shipping there is 1 to 2 business days and costs $14.99 per order.
Alaska and Hawaii use standard shipping only, take 5 to 8 business days, and add a $14.99 surcharge to the $4.99 standard fee, for a $19.98 total shipping charge.
Standard is the lowest-priced supported shipping option. For an existing order, quote the actual recorded promised date and charge rather than recalculating historical checkout charges.
The agent can explain options but cannot upgrade shipping, collect a fee, or arrange a shipment.

## S4 Exceptions

International destinations, territories outside the 50 states, delivery guarantees, and shipping-fee exceptions require Delivery staff.
An order without a recorded shipping charge does not prove shipping was free. Do not add the illustrative schedule to its refund amount.
