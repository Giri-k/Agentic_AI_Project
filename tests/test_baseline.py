"""Baseline tests: no network. A fake model and a fake retriever stand in; tools and store are the real ones."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.baseline import BaselineOutput, IntendedAction, resume, run_ticket
from agent.schemas import Category, Status, ToolError
from agent.staff import approve
from agent.store import Store
from conftest import tickets_of

DELIVERY_TICKET = "T-0029"   # plain tracking question
DEMO_TICKET = "T-0039"       # refund; return received like new; no approval until staff adds APR-8013
INJECTION_TICKET = "T-0095"  # ticket text claims an approval that does not exist
DEMO_REFUND = dict(order_id="O-60048", cents=114900, approval_id="APR-8013")
FALSE_REFUND_CLAIM = "Your refund has been issued."
FALSE_CANCEL_CLAIM = "Your order has been cancelled."


class FakeLLM:
    """Returns canned outputs (or raises them) and counts calls."""
    model = "fake"

    def __init__(self, *outputs):
        self.outputs, self.calls, self.last_messages = list(outputs), 0, None

    def parse(self, messages, schema):
        self.calls += 1
        self.last_messages = messages
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def fake_retriever(query, k):
    """Same shape the real retriever returns: passages with source IDs, scores, method."""
    return {"passages": [{"source_id": "POL-RET-02", "text": "Refunds need staff approval.", "score": 0.9}],
            "method": "fake", "weak_evidence": False}


def down_retriever(query, k):
    raise ToolError("KB_UNAVAILABLE", "policy search is down", retryable=True)


def exploding_retriever(query, k):
    raise RuntimeError("boom")


def action(tool, **kw):
    return IntendedAction(tool=tool, args_json=json.dumps(kw))


def output(**kw):
    base = dict(category=Category.RETURNS_REFUNDS, status=Status.RESOLVED, response="Done.", citations=["POL-RET-02"],
                intended_actions=[], unresolved_requirements=[], stop_reason="done")
    base.update(kw)
    return BaselineOutput(**base)


def events(store, ticket_id):
    path = Path(store.root) / "traces" / f"{ticket_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def requests(store, ticket_id, tool):
    return [e for e in events(store, ticket_id) if e["event"] == "tool_request" and e["tool"] == tool]


def tool_errors(store, ticket_id, tool):
    """Error codes of every failed result for one tool, in order."""
    return [e["error"]["code"] for e in events(store, ticket_id)
            if e["event"] == "tool_result" and e["tool"] == tool and not e["ok"]]


def model_calls(store, ticket_id):
    return [e for e in events(store, ticket_id) if e["event"] == "model_call"]


def sent_messages(store, ticket_id):
    return [m for m in store.data["outbox"] if m["ticket_id"] == ticket_id]


def cancellable_ticket(store):
    """A cancellation ticket whose order has not shipped and which has no injected failure."""
    return next(t for t in tickets_of("C1") if not store.failure_injection(t))


def validation_error():
    with pytest.raises(ValidationError) as err:
        BaselineOutput.model_validate({})
    return err.value


def refund_and_claim():
    """What a naive model plans: refund, then tell the customer it worked."""
    return output(status=Status.RESOLVED, response=FALSE_REFUND_CLAIM,
                  intended_actions=[action("process_refund", **DEMO_REFUND),
                                    action("send_message", ticket_id=DEMO_TICKET, body=FALSE_REFUND_CLAIM,
                                           citations=["POL-RET-02"])])


def assert_unfinished_and_silent(store, ticket_id, result, llm):
    """Shared checks for every failed-action case: failed_safe, no reply, one model call, saved as failed_safe."""
    assert result.status == Status.FAILED_SAFE
    assert result.response is None and result.citations == []
    assert not any(r.startswith("MSG-") for r in result.receipt_ids)
    assert sent_messages(store, ticket_id) == []
    assert requests(store, ticket_id, "send_message") == []
    assert store.ticket_state(ticket_id)["status"] == "failed_safe"
    assert llm.calls == 1 and len(model_calls(store, ticket_id)) == 1


# --- the successful paths still work ----------------------------------------------------------

def test_happy_path_delivery_ticket_is_resolved_with_a_message_receipt(store):
    reply = "Your order is in transit and expected soon."
    llm = FakeLLM(output(category=Category.DELIVERY, status=Status.RESOLVED, response=reply, citations=["POL-SHIP-01"],
                         intended_actions=[action("send_message", ticket_id=DELIVERY_TICKET, body=reply,
                                                  citations=["POL-SHIP-01"])]))
    result = run_ticket(store, DELIVERY_TICKET, fake_retriever, llm)
    assert result.status == Status.RESOLVED
    assert any(r.startswith("MSG-") for r in result.receipt_ids)
    assert store.ticket_state(DELIVERY_TICKET)["status"] == "resolved"
    assert llm.calls == 1


def test_successful_refund_sends_the_reply_and_resolves(store):
    approve(store, "APR-8013")
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(refund_and_claim())
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert result.status == Status.RESOLVED and result.response == FALSE_REFUND_CLAIM
    assert any(r.startswith("RF-") for r in result.receipt_ids) and any(r.startswith("MSG-") for r in result.receipt_ids)
    assert len(store.data["refunds"]) == refunds_before + 1
    assert len(sent_messages(store, DEMO_TICKET)) == 1
    assert store.ticket_state(DEMO_TICKET)["status"] == "resolved"
    assert llm.calls == 1 and len(model_calls(store, DEMO_TICKET)) == 1


def test_successful_cancellation_sends_the_reply_and_resolves(store):
    tid = cancellable_ticket(store)
    order_id = store.tickets[tid]["order_ids"][0]
    llm = FakeLLM(output(category=Category.CANCELLATION, status=Status.RESOLVED, response=FALSE_CANCEL_CLAIM,
                         citations=["POL-ORD-01"],
                         intended_actions=[action("cancel_order", order_id=order_id),
                                           action("send_message", ticket_id=tid, body=FALSE_CANCEL_CLAIM,
                                                  citations=["POL-ORD-01"])]))
    cancellations_before = len(store.data["cancellations"])
    result = run_ticket(store, tid, fake_retriever, llm)
    assert result.status == Status.RESOLVED
    assert any(r.startswith("CAN-") for r in result.receipt_ids) and any(r.startswith("MSG-") for r in result.receipt_ids)
    assert len(store.data["cancellations"]) == cancellations_before + 1
    assert store.order(order_id)["status"] == "cancelled"
    assert store.ticket_state(tid)["status"] == "resolved"
    assert llm.calls == 1


def test_a_model_that_pauses_without_proposing_a_refund_still_saves_awaiting_approval(store):
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=["staff approval for 114900 cents"],
                         stop_reason="needs_staff_approval",
                         intended_actions=[action("send_message", ticket_id=DEMO_TICKET, body="Waiting on staff approval.",
                                                  citations=["POL-RET-02"])]))
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert result.status == Status.AWAITING_APPROVAL
    assert any(r.startswith("MSG-") for r in result.receipt_ids)
    assert len(store.data["refunds"]) == refunds_before
    assert store.ticket_state(DEMO_TICKET)["status"] == "awaiting_approval"


def test_resume_after_staff_approval_refunds_once_and_never_twice(store):
    pause = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=["staff approval for 114900 cents"],
                           stop_reason="needs_staff_approval"))
    run_ticket(store, DEMO_TICKET, fake_retriever, pause)
    approve(store, "APR-8013")

    result = resume(store, DEMO_TICKET, fake_retriever, FakeLLM(refund_and_claim()))
    assert result.status == Status.RESOLVED
    assert any(r.startswith("RF-") for r in result.receipt_ids)
    assert len(store.refunds(approval_id="APR-8013")) == 1

    again = resume(store, DEMO_TICKET, fake_retriever, FakeLLM(refund_and_claim()))  # saved receipts replay, no second refund
    assert len(store.refunds(approval_id="APR-8013")) == 1
    assert again.status == Status.FAILED_SAFE  # resolved cannot be saved again; the store keeps the real state
    assert store.ticket_state(DEMO_TICKET)["status"] == "resolved"


# --- a failed refund or cancellation stops the run and drops the planned reply --------------

def test_rejected_refund_sends_no_success_message_and_saves_failed_safe(store):
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(refund_and_claim())  # no approval exists yet, so the tool refuses
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert tool_errors(store, DEMO_TICKET, "process_refund") == ["NO_MATCHING_APPROVAL"]
    assert len(store.data["refunds"]) == refunds_before
    assert result.stop_reason == "baseline_action_failed_process_refund_NO_MATCHING_APPROVAL"
    assert result.unresolved_requirements == [f"staff recovery: {result.stop_reason}"]
    assert_unfinished_and_silent(store, DEMO_TICKET, result, llm)
    saved = store.ticket_state(DEMO_TICKET)["last_result"]
    assert saved["status"] == "failed_safe" and saved["stop_reason"] == result.stop_reason and saved["response"] is None
    stopped = [e for e in events(store, DEMO_TICKET) if e["event"] == "actions_stopped"]
    assert stopped and stopped[0]["skipped"] == ["send_message"] and stopped[0]["reply_suppressed"] is True


def test_rejected_cancellation_sends_no_success_message_and_does_not_resolve(store):
    cancellations_before = len(store.data["cancellations"])
    order_status = store.order("O-60048")["status"]
    llm = FakeLLM(output(category=Category.CANCELLATION, status=Status.RESOLVED, response=FALSE_CANCEL_CLAIM,
                         citations=["POL-ORD-01"],
                         intended_actions=[action("cancel_order", order_id="O-60048"),  # already shipped and returned
                                           action("send_message", ticket_id=DEMO_TICKET, body=FALSE_CANCEL_CLAIM,
                                                  citations=["POL-ORD-01"])]))
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    codes = tool_errors(store, DEMO_TICKET, "cancel_order")
    assert codes and codes[0] in ("ALREADY_SHIPPED", "NOT_CANCELLABLE")
    assert len(store.data["cancellations"]) == cancellations_before
    assert store.order("O-60048")["status"] == order_status
    assert result.stop_reason == f"baseline_action_failed_cancel_order_{codes[0]}"
    assert result.status != Status.RESOLVED and not any(r.startswith("CAN-") for r in result.receipt_ids)
    assert_unfinished_and_silent(store, DEMO_TICKET, result, llm)


def test_refund_timeout_after_the_refund_committed_keeps_the_receipt_and_never_duplicates(store):
    approve(store, "APR-8013")
    store.tickets[DEMO_TICKET]["failure_injection"] = dict(tool="process_refund", error="TIMEOUT", retryable=True, fail_times=1)
    llm = FakeLLM(refund_and_claim())
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)

    assert tool_errors(store, DEMO_TICKET, "process_refund") == ["TIMEOUT"]  # the reply was lost...
    committed = store.refunds(approval_id="APR-8013")
    assert len(committed) == 1                                              # ...but the refund really happened, once
    refund_receipt = committed[0]["refund_id"]
    assert len(requests(store, DEMO_TICKET, "process_refund")) == 1         # no retry, so no second attempt
    assert result.stop_reason == "baseline_action_failed_process_refund_TIMEOUT"
    assert refund_receipt in result.receipt_ids                             # the outcome does not claim nothing happened
    assert_unfinished_and_silent(store, DEMO_TICKET, result, llm)

    reloaded = Store(store.root)                                            # what is on disk, not what is in memory
    assert len(reloaded.refunds(approval_id="APR-8013")) == 1
    state = reloaded.ticket_state(DEMO_TICKET)
    assert state["status"] == "failed_safe" and refund_receipt in state["last_result"]["receipt_ids"]
    assert refund_receipt in [a["receipt_id"] for a in state["actions"]]
    assert [m for m in reloaded.data["outbox"] if m["ticket_id"] == DEMO_TICKET] == []


def test_earlier_success_then_failure_keeps_the_earlier_receipt_and_stops_later_actions(store):
    tid = cancellable_ticket(store)
    order_id = store.tickets[tid]["order_ids"][0]
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(output(category=Category.CANCELLATION, status=Status.RESOLVED, response=FALSE_CANCEL_CLAIM,
                         citations=["POL-ORD-01"],
                         intended_actions=[action("cancel_order", order_id=order_id),
                                           action("process_refund", order_id=order_id, cents=100, approval_id="APR-9999"),
                                           action("send_message", ticket_id=tid, body=FALSE_CANCEL_CLAIM,
                                                  citations=["POL-ORD-01"])]))
    result = run_ticket(store, tid, fake_retriever, llm)
    cancelled = store.data["cancellations"][-1]
    assert cancelled["order_id"] == order_id and store.order(order_id)["status"] == "cancelled"  # the earlier action stands
    assert cancelled["cancellation_id"] in result.receipt_ids
    assert cancelled["cancellation_id"] in [a["receipt_id"] for a in store.ticket_state(tid)["actions"]]
    assert tool_errors(store, tid, "process_refund") == ["NO_MATCHING_APPROVAL"]
    assert len(store.data["refunds"]) == refunds_before
    assert result.stop_reason == "baseline_action_failed_process_refund_NO_MATCHING_APPROVAL"
    assert_unfinished_and_silent(store, tid, result, llm)  # the reply that came after the failure never ran


def test_a_failure_stops_later_changes_too_not_just_the_reply(store):
    tid = cancellable_ticket(store)
    order_id = store.tickets[tid]["order_ids"][0]
    cancellations_before = len(store.data["cancellations"])
    llm = FakeLLM(output(category=Category.CANCELLATION, status=Status.RESOLVED, response=FALSE_CANCEL_CLAIM,
                         citations=["POL-ORD-01"],
                         intended_actions=[action("process_refund", order_id=order_id, cents=100, approval_id="APR-9999"),
                                           action("cancel_order", order_id=order_id),
                                           action("send_message", ticket_id=tid, body=FALSE_CANCEL_CLAIM,
                                                  citations=["POL-ORD-01"])]))
    result = run_ticket(store, tid, fake_retriever, llm)
    assert requests(store, tid, "cancel_order") == []                 # never attempted after the refund failed
    assert len(store.data["cancellations"]) == cancellations_before
    assert store.order(order_id)["status"] == "processing"
    assert [e["skipped"] for e in events(store, tid) if e["event"] == "actions_stopped"] == [["cancel_order", "send_message"]]
    assert_unfinished_and_silent(store, tid, result, llm)


# --- other failure and safety behavior (unchanged) ----------------------------------------------

def test_invalid_model_output_fails_safe_with_exactly_one_model_call(store):
    llm = FakeLLM(validation_error())
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "baseline_invalid_output"
    assert llm.calls == 1 and len(model_calls(store, DEMO_TICKET)) == 1
    assert store.ticket_state(DEMO_TICKET)["status"] == "failed_safe"


def test_status_that_breaks_ticket_result_rules_fails_safe(store):
    llm = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=[]))
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "baseline_invalid_result"
    assert store.ticket_state(DEMO_TICKET)["status"] == "failed_safe"


@pytest.mark.parametrize("retriever", [down_retriever, exploding_retriever])
def test_kb_failure_means_no_passages_and_no_retry(store, retriever):
    llm = FakeLLM(output(status=Status.ESCALATED, unresolved_requirements=["no policy evidence"],
                         stop_reason="unsupported_request"))
    result = run_ticket(store, DEMO_TICKET, retriever, llm)
    assert result.status == Status.ESCALATED
    assert llm.calls == 1 and len(requests(store, DEMO_TICKET, "kb_lookup")) == 1
    seen = json.loads(llm.last_messages[1]["content"])
    assert seen["policy_passages"] == [] and "no policy evidence" in seen["policy_search"]


def test_injected_approval_in_ticket_text_never_produces_a_refund(store):
    order_id = store.tickets[INJECTION_TICKET]["order_ids"][0]
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=["staff approval"],
                         intended_actions=[action("process_refund", order_id=order_id, cents=100, approval_id="APR-9999")]))
    result = run_ticket(store, INJECTION_TICKET, fake_retriever, llm)
    assert len(store.data["refunds"]) == refunds_before
    assert tool_errors(store, INJECTION_TICKET, "process_refund") == ["NO_MATCHING_APPROVAL"]
    assert result.status == Status.FAILED_SAFE and store.ticket_state(INJECTION_TICKET)["status"] == "failed_safe"


def test_model_cannot_choose_python_controlled_tools():
    for tool in ("ticket_get", "kb_lookup", "ticket_update"):
        with pytest.raises(ValidationError):
            IntendedAction(tool=tool, args_json="{}")


@pytest.mark.parametrize("ticket_id", [DELIVERY_TICKET, DEMO_TICKET, INJECTION_TICKET])
def test_every_run_is_exactly_one_retrieval_and_one_model_call(store, ticket_id):
    llm = FakeLLM(output(status=Status.ESCALATED, unresolved_requirements=["staff decision"], stop_reason="unsupported_request"))
    run_ticket(store, ticket_id, fake_retriever, llm)
    assert llm.calls == 1
    assert len(requests(store, ticket_id, "kb_lookup")) == 1
    assert len(requests(store, ticket_id, "ticket_get")) == 1
    assert len(requests(store, ticket_id, "ticket_update")) == 1
    assert len(model_calls(store, ticket_id)) == 1
    assert model_calls(store, ticket_id)[0]["prompt_version"] == "baseline_v1"


# --- malformed model output is injected for the baseline exactly as for the agent ------------------

@pytest.mark.parametrize("ticket_id", ["T-0045", "T-0091"])
def test_injected_malformed_output_fails_the_single_pass_safely_without_calling_the_model(store, ticket_id):
    assert (store.failure_injection(ticket_id) or {}).get("stage") == "model"
    llm = FakeLLM(output())
    result = run_ticket(store, ticket_id, fake_retriever, llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "baseline_invalid_output"
    assert llm.calls == 0                                   # simulated: no request was sent
    calls = model_calls(store, ticket_id)
    assert len(calls) == 1 and calls[0]["simulated"] is True and calls[0]["ok"] is False
    assert store.ticket_state(ticket_id)["status"] == "failed_safe"
    assert requests(store, ticket_id, "send_message") == []
