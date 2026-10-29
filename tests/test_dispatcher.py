"""Dispatcher rules: free refusals, budgets, the one-retry rule, action keys and injected failures."""

import pytest

from agent.config import MAX_TOOL_CALLS
from agent.dispatcher import Budget, Dispatcher
from agent.schemas import StopRun
from agent.staff import approve
from conftest import LABELS, args, tickets_of

DEMO = args(order_id="O-60048", cents=114900, approval_id="APR-8013")


def injected(store, tool, error="TIMEOUT"):
    """First ticket whose injected failure targets this tool with this error."""
    return next(t for t in store.tickets
                if (store.failure_injection(t) or {}).get("tool") == tool
                and store.failure_injection(t)["error"] == error)


def test_bad_tool_names_and_bad_arguments_cost_no_budget(store):
    d = Dispatcher(store, "T-0039")
    assert d.call("refund_now", "{}")["error"]["code"] == "UNKNOWN_TOOL"
    assert d.call("process_refund", args(order_id="O-60048", cents="114900", approval_id="APR-8013"))["error"]["code"] == "INVALID_ARGS"
    assert d.call("ticket_get", args(ticket_id="T-0039", extra=1))["error"]["code"] == "INVALID_ARGS"
    assert d.budget.tool_calls == 0


def test_third_identical_call_stops_even_if_it_is_invalid(store):
    d = Dispatcher(store, "T-0039")
    d.call("refund_now", "{}")
    d.call("refund_now", "{}")
    with pytest.raises(StopRun, match="repeated_call"):
        d.call("refund_now", "{}")


def test_same_arguments_in_another_key_order_count_as_identical(store):
    d = Dispatcher(store, "T-0039")
    d.call("ticket_get", '{"ticket_id": "T-0039"}')
    d.call("ticket_get", '{"ticket_id":"T-0039"}')
    with pytest.raises(StopRun):
        d.call("ticket_get", '{ "ticket_id" : "T-0039" }')


def test_tool_budget_stops_the_seventh_real_call(store):
    d = Dispatcher(store, "T-0039")
    for i in range(MAX_TOOL_CALLS):
        d.call("send_message", args(ticket_id="T-0039", body=f"update {i}", citations=["POL-RET-02"]))
    with pytest.raises(StopRun, match="tool_budget"):
        d.call("send_message", args(ticket_id="T-0039", body="one more", citations=["POL-RET-02"]))


def test_repeated_refund_returns_the_saved_receipt_not_a_second_refund(store):
    approve(store, "APR-8013")
    d = Dispatcher(store, "T-0039")
    first = d.call("process_refund", DEMO)["result"]
    second = d.call("process_refund", args(approval_id="APR-8013", cents=114900, order_id="O-60048"))["result"]
    assert second["replayed"] and second["receipt_id"] == first["receipt_id"]
    assert len([r for r in store.refunds("O-60048") if r["status"] == "issued"]) == 1


def test_timeout_after_refund_write_never_refunds_twice(store):
    tid = injected(store, "process_refund")
    expected = LABELS[tid]["expected_tool_args"]["process_refund"]
    d = Dispatcher(store, tid)
    first = d.call("process_refund", args(**expected))
    retry = d.call("process_refund", args(**expected))
    assert first["error"]["retryable"] is True and retry["error"]["retryable"] is False
    assert len([r for r in store.refunds(expected["order_id"]) if r["status"] == "issued"]) == 1
    assert d.save_failed_safe(LABELS[tid]["category"], "refund_reply_lost")["status"] == "failed_safe"


def test_one_timeout_then_model_retry_succeeds_and_retry_is_spent(store):
    tid = next(t for t in store.tickets if (store.failure_injection(t) or {}).get("tool") == "ticket_get"
               and store.failure_injection(t)["fail_times"] == 1)
    d = Dispatcher(store, tid)
    assert d.call("ticket_get", args(ticket_id=tid))["error"]["code"] == "TIMEOUT"
    assert d.call("ticket_get", args(ticket_id=tid))["ok"]
    assert d.budget.retries == 1 and d.budget.tool_calls == 2


def test_version_conflict_is_injected_before_anything_is_saved(store):
    tid = injected(store, "ticket_update", "VERSION_CONFLICT")
    d = Dispatcher(store, tid)
    lab = LABELS[tid]
    result = dict(ticket_id=tid, category=lab["category"], status="escalated", response="Passed to staff.",
                  citations=lab["policy_ids"], receipt_ids=[], unresolved_requirements=["staff review"],
                  stop_reason="test")
    version = store.ticket_state(tid)["version"]
    assert d.call("ticket_update", args(result=result, expected_version=version))["error"]["code"] == "VERSION_CONFLICT"
    assert store.ticket_state(tid)["version"] == version
    assert d.call("ticket_update", args(result=result, expected_version=version))["ok"]


def test_budget_survives_a_checkpoint_round_trip(store):
    d = Dispatcher(store, "T-0039")
    d.call("ticket_get", args(ticket_id="T-0039"))
    resumed = Dispatcher(store, "T-0039", budget=Budget.from_dict(d.budget.to_dict()))
    resumed.call("ticket_get", args(ticket_id="T-0039"))
    assert resumed.budget.tool_calls == 2
    with pytest.raises(StopRun, match="repeated_call"):
        resumed.call("ticket_get", args(ticket_id="T-0039"))


def test_unknown_ticket_cannot_get_a_dispatcher(store):
    with pytest.raises(StopRun, match="unknown_ticket"):
        Dispatcher(store, "T-9999")
