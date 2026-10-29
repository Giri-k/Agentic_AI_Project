"""Each tool's own checks, called directly (no dispatcher)."""

import pytest
from pydantic import ValidationError

from agent.schemas import StopRun, TicketResult, ToolError
from agent.staff import approve
from agent.tools import (CancelOrderArgs, Context, ProcessRefundArgs, SendMessageArgs, TicketGetArgs,
                         TicketUpdateArgs, cancel_order, process_refund, send_message, ticket_get, ticket_update)
from conftest import LABELS, args, tickets_of


def ctx_for(store, ticket_id):
    """Context exactly as the dispatcher builds it."""
    return Context(ticket_id=ticket_id, customer_id=store.tickets[ticket_id]["customer_id"])


def refund(store, ticket_id, **kw):
    """Call process_refund with model-style JSON arguments."""
    return process_refund(store, ctx_for(store, ticket_id), ProcessRefundArgs.model_validate_json(args(**kw)))


def result_json(ticket_id, status, **kw):
    """A TicketResult as JSON, with harmless defaults for fields a test doesn't care about."""
    base = dict(ticket_id=ticket_id, category="returns_refunds", status=status, response="Thanks, all done.",
                citations=["POL-RET-02"], receipt_ids=[], unresolved_requirements=[], stop_reason="done")
    base.update(kw)
    return base


def update(store, ticket_id, **kw):
    """Call ticket_update at the ticket's current version."""
    version = store.ticket_state(ticket_id)["version"]
    payload = TicketUpdateArgs.model_validate_json(args(result=result_json(ticket_id, **kw), expected_version=version))
    return ticket_update(store, ctx_for(store, ticket_id), payload)


# --- TicketResult ------------------------------------------------------------------

def test_ticket_result_rejects_extra_fields_and_contradictions():
    with pytest.raises(ValidationError):
        TicketResult.model_validate_json(args(**result_json("T-0039", "resolved"), team="Returns"))
    with pytest.raises(ValidationError):
        TicketResult.model_validate_json(args(**result_json("T-0039", "resolved", unresolved_requirements=["x"])))
    with pytest.raises(ValidationError):
        TicketResult.model_validate_json(args(**result_json("T-0039", "escalated")))


# --- ticket_get ---------------------------------------------------------------------

def test_ticket_get_hides_injected_failures_and_other_customers_orders(store):
    injected = next(t for t in store.tickets if store.failure_injection(t))
    view = ticket_get(store, ctx_for(store, injected), TicketGetArgs(ticket_id=injected))
    assert "failure_injection" not in view["ticket"]

    scope = next(t for t, lab in LABELS.items() if lab.get("scope_violation"))
    view = ticket_get(store, ctx_for(store, scope), TicketGetArgs(ticket_id=scope))
    assert view["orders"] == [] and view["orders_not_on_this_account"]


def test_ticket_get_other_ticket_stops_the_run(store):
    with pytest.raises(StopRun):
        ticket_get(store, ctx_for(store, "T-0039"), TicketGetArgs(ticket_id="T-0040"))


# --- process_refund -----------------------------------------------------------------

def test_demo_refund_needs_staff_approval_and_uses_it_once(store):
    demo = dict(order_id="O-60048", cents=114900, approval_id="APR-8013")
    with pytest.raises(ToolError, match="NO_MATCHING_APPROVAL|exact order"):
        refund(store, "T-0039", **demo)
    approve(store, "APR-8013")
    with pytest.raises(ToolError) as wrong_amount:
        refund(store, "T-0039", **{**demo, "cents": 100000})
    assert wrong_amount.value.code == "NO_MATCHING_APPROVAL"
    assert refund(store, "T-0039", **demo)["status"] == "issued"
    with pytest.raises(ToolError) as again:
        refund(store, "T-0039", **demo)
    assert again.value.code == "APPROVAL_ALREADY_USED"


@pytest.mark.parametrize("ticket_id", tickets_of("R8"))
def test_refund_above_remaining_balance_is_refused(store, ticket_id):
    order_id = store.tickets[ticket_id]["order_ids"][0]
    unused = next(a for a in store.approvals(order_id) if not store.refunds(approval_id=a["approval_id"]))
    with pytest.raises(ToolError) as err:
        refund(store, ticket_id, order_id=order_id, cents=unused["amount_cents"], approval_id=unused["approval_id"])
    assert err.value.code == "EXCEEDS_REMAINING_BALANCE"


def test_refund_on_another_customers_order_looks_like_missing_order(store):
    other_order = next(o for o in store.data["orders"] if o["customer_id"] != store.tickets["T-0039"]["customer_id"])
    with pytest.raises(ToolError) as err:
        refund(store, "T-0039", order_id=other_order["order_id"], cents=100, approval_id="APR-8001")
    assert err.value.code == "ORDER_NOT_FOUND"


# --- cancel_order -------------------------------------------------------------------

@pytest.mark.parametrize("ticket_id", tickets_of("C1"))
def test_cancel_unshipped_order_reverses_payment(store, ticket_id):
    order_id = LABELS[ticket_id]["expected_tool_args"]["cancel_order"]["order_id"]
    receipt = cancel_order(store, ctx_for(store, ticket_id), CancelOrderArgs(order_id=order_id))
    assert receipt["receipt_id"].startswith("CAN-") and receipt["payment_reversed_cents"] > 0
    assert store.order(order_id)["status"] == "cancelled"


@pytest.mark.parametrize("ticket_id", tickets_of("C2"))
def test_cancel_shipped_order_is_refused(store, ticket_id):
    order_id = store.tickets[ticket_id]["order_ids"][0]
    with pytest.raises(ToolError) as err:
        cancel_order(store, ctx_for(store, ticket_id), CancelOrderArgs(order_id=order_id))
    assert err.value.code == "ALREADY_SHIPPED"


# --- send_message -------------------------------------------------------------------

def test_send_message_goes_to_stored_email_and_blocks_card_numbers(store):
    ctx = ctx_for(store, "T-0039")
    sent = send_message(store, ctx, SendMessageArgs.model_validate_json(
        args(ticket_id="T-0039", body="Your refund is waiting on staff approval.", citations=["POL-RET-02"])))
    assert sent["to"] == store.customer(ctx.customer_id)["email"]
    with pytest.raises(ToolError) as err:
        send_message(store, ctx, SendMessageArgs.model_validate_json(
            args(ticket_id="T-0039", body="Card 4111 1111 1111 1111 on file", citations=["POL-SEC-02"])))
    assert err.value.code == "CARD_NUMBER_IN_BODY"


# --- ticket_update ------------------------------------------------------------------

def test_resolved_needs_a_message_receipt(store):
    with pytest.raises(ToolError) as err:
        update(store, "T-0039", status="resolved")
    assert err.value.code == "INCOMPLETE"


def test_pause_saves_and_bumps_version_then_old_version_conflicts(store):
    saved = update(store, "T-0039", status="awaiting_approval", response=None,
                   unresolved_requirements=["staff approval for 114900 cents"])
    assert saved["status"] == "awaiting_approval" and saved["version"] == 2 and saved["team"] == "Returns"
    stale = TicketUpdateArgs.model_validate_json(args(result=result_json("T-0039", "escalated",
                                                 unresolved_requirements=["x"]), expected_version=1))
    with pytest.raises(ToolError) as err:
        ticket_update(store, ctx_for(store, "T-0039"), stale)
    assert err.value.code == "VERSION_CONFLICT"


def test_escalated_is_final_for_the_agent_and_fraud_goes_to_human_review(store):
    saved = update(store, "T-0039", status="escalated", stop_reason="fraud_review: repeated claims",
                   unresolved_requirements=["staff review"])
    assert saved["team"] == "Human Review"
    with pytest.raises(ToolError) as err:
        update(store, "T-0039", status="resolved")
    assert err.value.code == "INVALID_TRANSITION"


@pytest.mark.parametrize("ticket_id", tickets_of("M2"))
def test_customer_confirmation_closes_a_resolved_ticket(store, ticket_id):
    receipts = [a["receipt_id"] for a in store.ticket_state(ticket_id)["actions"]]
    assert update(store, ticket_id, status="closed", receipt_ids=receipts)["status"] == "closed"


def test_receipts_from_another_ticket_are_rejected(store):
    other = next(a["receipt_id"] for s in store.data["ticket_state"].values() for a in s["actions"])
    with pytest.raises(ToolError) as err:
        update(store, "T-0039", status="awaiting_approval", receipt_ids=[other], unresolved_requirements=["x"])
    assert err.value.code == "UNKNOWN_RECEIPT"
