"""The six agent tools. Each checks its own permissions against the store; the model can't skip a check.

Tools run under a Context that Python builds from the ticket: the customer is never a model argument,
and action keys (idempotency) are added by the dispatcher, not passed by the model.
"""

import re
from dataclasses import dataclass
from typing import Callable

from pydantic import Field

from .config import KB_MAX_K
from .schemas import Status, StopRun, Strict, TicketResult, ToolError, route_team

CARD_NUMBER = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Statuses the agent may leave; escalated and closed belong to staff from then on.
ALLOWED_NEXT = {
    "new": set(Status),
    "awaiting_customer": set(Status),
    "awaiting_approval": set(Status),
    "failed_safe": set(Status),
    "resolved": {Status.CLOSED, Status.AWAITING_CUSTOMER, Status.ESCALATED},
    "escalated": set(),
    "closed": set(),
}


@dataclass
class Context:
    """Who and what this run is for. Built by Python from the ticket, never from model output."""
    ticket_id: str
    customer_id: str
    key: str | None = None
    retriever: Callable | None = None


# --- argument shapes (also the JSON schemas the model sees) --------------------

class KbLookupArgs(Strict):
    """kb_lookup(query, k): k is 1 to 3 passages."""
    query: str = Field(min_length=3)
    k: int = Field(ge=1, le=KB_MAX_K)


class TicketGetArgs(Strict):
    """ticket_get(ticket_id)."""
    ticket_id: str


class TicketUpdateArgs(Strict):
    """ticket_update(result, expected_version): the version guards against saving over newer changes."""
    result: TicketResult
    expected_version: int


class SendMessageArgs(Strict):
    """send_message(ticket_id, body, citations): every reply cites at least one policy."""
    ticket_id: str
    body: str = Field(min_length=1)
    citations: list[str] = Field(min_length=1)


class ProcessRefundArgs(Strict):
    """process_refund(order_id, cents, approval_id): cents must be a positive whole number."""
    order_id: str
    cents: int = Field(gt=0)
    approval_id: str


class CancelOrderArgs(Strict):
    """cancel_order(order_id)."""
    order_id: str


# --- helpers ---------------------------------------------------------------------

def _owned_order(store, ctx, order_id):
    """Another customer's order looks exactly like a missing one (POL-SEC-02)."""
    order = store.order(order_id)
    if order is None or order["customer_id"] != ctx.customer_id:
        raise ToolError("ORDER_NOT_FOUND", f"no order {order_id} on this customer's account")
    return order


def _check_ticket(ctx, ticket_id):
    """A run may only touch its own ticket."""
    if ticket_id != ctx.ticket_id:
        raise ToolError("WRONG_TICKET", f"this run handles {ctx.ticket_id} only")


def _order_view(store, order):
    """Order plus everything the agent needs to decide: payments, approvals (and if used), refunds, balance."""
    oid = order["order_id"]
    approvals = [{**a, "used_by_refund": next((r["refund_id"] for r in store.refunds(approval_id=a["approval_id"])), None)}
                 for a in store.approvals(oid)]
    return {**order, "payments": store.payments(oid), "approvals": approvals, "refunds": store.refunds(oid),
            "remaining_refundable_cents": store.remaining_refundable_cents(oid)}


def _refund_basis(store, order, approval):
    """What makes this refund eligible, beyond the staff approval itself."""
    if store.captured_cents(order["order_id"]) > order["total_cents"]:
        return "duplicate_capture"  # POL-BIL-02: a billing correction, allowed even for final sale
    if any(item.get("final_sale") for item in order["items"]):
        raise ToolError("NOT_ELIGIBLE", "final-sale item: no merchandise refund (POL-RET-04); escalate any conflict")
    ret = order.get("return")
    if ret is not None:
        if not ret.get("received_at"):
            raise ToolError("NOT_ELIGIBLE", "the return has not been received yet (POL-RET-01)")
        if ret.get("condition") != "like_new":
            raise ToolError("NOT_ELIGIBLE", "returned item is not in like-new condition (POL-RET-01)")
        return "return_received"
    if approval.get("reason"):
        return "staff_recorded_basis"  # e.g. non-delivery or damage, decided by staff
    raise ToolError("NOT_ELIGIBLE", "no return received and no staff-recorded refund basis")


# --- tools -----------------------------------------------------------------------

def kb_lookup(store, ctx, args):
    """Policy search. The retriever (embeddings + BM25 fallback) is plugged in by the next build step."""
    if ctx.retriever is None:
        raise ToolError("KB_UNAVAILABLE", "policy search is not configured", retryable=False)
    return ctx.retriever(args.query, args.k)


def ticket_get(store, ctx, args):
    """Read the ticket and only the orders on this customer's account; other orders are listed as not accessible."""
    if args.ticket_id != ctx.ticket_id:
        raise StopRun("unknown_ticket")  # spec: an unknown ticket ID stops the run
    ticket = store.ticket_for_agent(ctx.ticket_id)
    state = store.ticket_state(ctx.ticket_id)
    orders, not_accessible = [], []
    for oid in ticket["order_ids"]:
        order = store.order(oid)
        if order is not None and order["customer_id"] == ctx.customer_id:
            orders.append(_order_view(store, order))
        else:
            not_accessible.append(oid)
    return {"as_of": store.as_of, "ticket": ticket, "customer_name": store.customer(ctx.customer_id)["name"],
            "status": state["status"], "version": state["version"], "actions": state["actions"],
            "orders": orders, "orders_not_on_this_account": not_accessible}


def send_message(store, ctx, args):
    """Write a reply to the simulated outbox, always to the customer's stored email."""
    _check_ticket(ctx, args.ticket_id)
    if CARD_NUMBER.search(args.body):
        raise ToolError("CARD_NUMBER_IN_BODY", "remove the card number before sending (POL-SEC-02)")
    unknown = sorted(set(args.citations) - store.policy_ids)
    if unknown:
        raise ToolError("UNKNOWN_POLICY", f"unknown policy ids {unknown}")
    message_id = store.next_id("message")
    to = store.customer(ctx.customer_id)["email"]  # never an address from the ticket text
    store.data["outbox"].append({"message_id": message_id, "ticket_id": ctx.ticket_id, "to": to,
                                 "body": args.body, "citations": args.citations, "sent_at": store.as_of})
    return {"receipt_id": message_id, "to": to, "status": "sent"}


def process_refund(store, ctx, args):
    """Refund only with an exact, unused staff approval, within the remaining balance, on an eligible order.
    Every check runs before anything is written."""
    order = _owned_order(store, ctx, args.order_id)
    approval = store.approval(args.approval_id)
    if approval is None or approval["order_id"] != args.order_id or approval["amount_cents"] != args.cents:
        raise ToolError("NO_MATCHING_APPROVAL",
                        "no staff approval for this exact order and amount; use awaiting_approval (POL-RET-02)")
    if store.refunds(approval_id=args.approval_id):
        raise ToolError("APPROVAL_ALREADY_USED", "this approval was already used by another refund")
    remaining = store.remaining_refundable_cents(args.order_id)
    if args.cents > remaining:
        raise ToolError("EXCEEDS_REMAINING_BALANCE",
                        f"only {remaining} cents remain refundable; staff must reconcile (POL-RET-06)")
    basis = _refund_basis(store, order, approval)
    refund = {"refund_id": store.next_id("refund"), "order_id": args.order_id, "amount_cents": args.cents,
              "approval_id": args.approval_id, "created_at": store.as_of, "status": "issued"}
    store.data["refunds"].append(refund)
    if store.remaining_refundable_cents(args.order_id) == 0:
        order["status"] = "refunded"
    return {"receipt_id": refund["refund_id"], **refund, "basis": basis}


def cancel_order(store, ctx, args):
    """Cancel an owned order that has not shipped and reverse its payment (team decision, POL-ORD-01)."""
    order = _owned_order(store, ctx, args.order_id)
    if order["status"] != "processing" or order.get("shipment") is not None:
        raise ToolError("ALREADY_SHIPPED" if order.get("shipment") else "NOT_CANCELLABLE",
                        f"order is {order['status']}; only unshipped orders can be cancelled (POL-ORD-01)")
    reversed_cents = 0
    for payment in store.payments(args.order_id):
        if payment["status"] == "captured":
            payment["status"] = "reversed"
            reversed_cents += payment["amount_cents"]
    order["status"] = "cancelled"
    cancellation = {"cancellation_id": store.next_id("cancellation"), "order_id": args.order_id,
                    "payment_reversed_cents": reversed_cents, "created_at": store.as_of}
    store.data["cancellations"].append(cancellation)
    return {"receipt_id": cancellation["cancellation_id"], **cancellation}


def ticket_update(store, ctx, args):
    """Save the final TicketResult after checking version, allowed transition, citations and receipts."""
    result, state = args.result, store.ticket_state(ctx.ticket_id)
    _check_ticket(ctx, result.ticket_id)
    if args.expected_version != state["version"]:
        raise ToolError("VERSION_CONFLICT", f"ticket is at version {state['version']}; reread it with ticket_get")
    if result.status not in ALLOWED_NEXT[state["status"]]:
        raise ToolError("INVALID_TRANSITION", f"cannot move from {state['status']} to {result.status.value}")
    unknown = sorted(set(result.citations) - store.policy_ids)
    if unknown:
        raise ToolError("UNKNOWN_POLICY", f"unknown policy ids {unknown}")
    own_receipts = {a["receipt_id"] for a in state["actions"]}
    foreign = sorted(set(result.receipt_ids) - own_receipts)
    if foreign:
        raise ToolError("UNKNOWN_RECEIPT", f"receipts {foreign} were not produced for this ticket")
    if result.status in (Status.RESOLVED, Status.CLOSED):
        if not result.response:
            raise ToolError("INCOMPLETE", "a resolved ticket needs the reply that was sent")
        if result.status == Status.RESOLVED and not any(r.startswith("MSG-") for r in result.receipt_ids):
            raise ToolError("INCOMPLETE", "resolved needs a send_message receipt: the customer must be told")
    if result.status == Status.CLOSED and store.tickets[ctx.ticket_id]["messages"][-1]["role"] != "customer":
        raise ToolError("INCOMPLETE", "closed needs the customer's confirmation as the latest message")
    audit_id = store.next_id("audit")
    team = route_team(result.category, result.status, result.stop_reason)
    state.update(status=result.status.value, version=state["version"] + 1, team=team.value,
                 last_result=result.model_dump(mode="json"))
    store.data["audit"].append({"audit_id": audit_id, "ticket_id": ctx.ticket_id, "status": result.status.value,
                                "version": state["version"], "team": team.value, "at": store.as_of})
    return {"receipt_id": audit_id, "status": result.status.value, "version": state["version"], "team": team.value}


@dataclass(frozen=True)
class ToolSpec:
    """One registry entry: the function, its argument model, and whether it changes the store."""
    fn: Callable
    args: type
    mutating: bool
    description: str


TOOLS = {
    "kb_lookup": ToolSpec(kb_lookup, KbLookupArgs, False,
                          "Search store policies. Returns up to k passages with policy IDs and scores."),
    "ticket_get": ToolSpec(ticket_get, TicketGetArgs, False,
                           "Read this ticket, its status and version, and the customer's linked orders, "
                           "payments, approvals and refunds."),
    "ticket_update": ToolSpec(ticket_update, TicketUpdateArgs, True,
                              "Save the final TicketResult. Ends the run. expected_version must match ticket_get."),
    "send_message": ToolSpec(send_message, SendMessageArgs, True,
                             "Send a reply to this ticket's customer, citing the policy IDs it relies on."),
    "process_refund": ToolSpec(process_refund, ProcessRefundArgs, True,
                               "Refund an order. Needs a staff approval for this exact order and amount."),
    "cancel_order": ToolSpec(cancel_order, CancelOrderArgs, True,
                             "Cancel an unshipped (processing) order and reverse its payment."),
}


def openai_tools():
    """Tool list for native function calling, generated from the argument models."""
    return [{"type": "function",
             "function": {"name": name, "description": spec.description, "strict": True,
                          "parameters": spec.args.model_json_schema()}}
            for name, spec in TOOLS.items()]
