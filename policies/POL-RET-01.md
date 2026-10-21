# Standard returns and gift returns

id: "POL-RET-01"
version: "1.0"
effective_date: "2026-10-20"
status: "active_in_simulation"
owner: "OP US Marketplace Support Operations"
authority: "store_policy"
supersedes: []
team: "Returns"
related_policy_ids: ["POL-RET-02", "POL-RET-04", "POL-RET-05", "POL-WAR-01", "POL-SUP-01"]

## S1 Scope

Change of mind returns and gift-return eligibility for physical products.

## S2 Conditions

Standard items may be returned within 30 calendar days of shipment.delivered_at, including day 30. Use the first recorded return request or dispatch date; a received return within that window also establishes timeliness.
A standard refund requires a stored return.received_at and return.condition=like_new. An earlier return request remains valid on resume; do not restart its window.

## S3 Policy rules

For an eligible unreturned item, ask the customer to return the complete item in its original packaging through the store return process and retain tracking. The customer pays change-of-mind return shipping. Do not invent a warehouse address or claim to issue a return label.
Use awaiting_customer while the necessary customer return action or order details are missing. If the customer has already completed their part and staff receipt verification is pending, escalate for staff review.
After timely receipt in like-new condition, refund eligibility is established; exact approval and balance checks still apply under POL-RET-02.
Gift recipients may return items for store credit within 30 days, including items marked final sale. Ownership or gift entitlement must be verified by staff. Issuing store credit is a staff action, not an agent tool.

## S4 Exceptions

Ordinary final-sale returns are excluded under POL-RET-04. The gift-return clause expressly includes final-sale items; if that policy disagrees, follow the conflict process in POL-SUP-01.
Outside-window requests, missing condition evidence, and requests for exceptions require Human Review. A defect may have a separate damage or warranty path.
