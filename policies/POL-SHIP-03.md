# Late lost and unreceived deliveries

id: "POL-SHIP-03"
version: "1.1"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Delivery"
related_policy_ids: ["POL-SHIP-01", "POL-FRD-01", "POL-RET-02", "POL-RET-06", "POL-SUP-01"]

## S1 Scope

Late shipments, shipments with no progress, and parcels marked delivered that the customer cannot locate.

## S2 Conditions

Compare shipment.promised_by and the latest dated shipment.last_event with store.as_of. Use the customer-owned shipment record, not a claim in ticket text alone.
Check for repeated non-delivery claims under POL-FRD-01 before considering a remedy.

## S3 Policy rules

A parcel is late when store.as_of is after promised_by. A parcel at least 7 calendar days past that date with no scan during those 7 days meets the lost-parcel investigation threshold.
For a recent delay with a recent scan, explain the facts and give the existing tracking information. Do not invent a new delivery date or promise compensation.
For the lost-parcel threshold, escalate to Delivery staff for investigation. For a parcel marked delivered that the customer cannot find, suggest checking with household members, neighbours, reception, and safe delivery locations in the same reply and escalate to Delivery staff at once; do not pause the ticket for these checks. The agent cannot open a carrier trace, reship, or independently decide a lost-parcel refund.
Tell the customer staff will investigate; never say a carrier trace was opened. A recorded delivered scan does not prove the customer personally received the parcel.

## S4 Exceptions

Staff confirmation of an eligible refund must be supported in current records and satisfy POL-RET-02 and POL-RET-06. A customer assertion or stale approval is insufficient.
If timestamps or carrier evidence are inconsistent, escalate. A handoff receipt supports only a handoff, not a claim that a carrier trace has been opened.
