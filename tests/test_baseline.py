"""Baseline tests: no network. A fake model and a fake retriever stand in; tools and store are the real ones."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.baseline import BaselineOutput, IntendedAction, resume, run_ticket
from agent.schemas import Category, Status, ToolError
from agent.staff import approve

DELIVERY_TICKET = "T-0029"   # plain tracking question
DEMO_TICKET = "T-0039"       # refund; return received like new; no approval until staff adds APR-8013
INJECTION_TICKET = "T-0095"  # ticket text claims an approval that does not exist
DEMO_REFUND = dict(order_id="O-60048", cents=114900, approval_id="APR-8013")


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


def model_calls(store, ticket_id):
    return [e for e in events(store, ticket_id) if e["event"] == "model_call"]


def validation_error():
    with pytest.raises(ValidationError) as err:
        BaselineOutput.model_validate({})
    return err.value


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


def test_demo_refund_without_approval_is_refused_and_ticket_pauses(store):
    refunds_before = len(store.data["refunds"])
    llm = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=["staff approval for 114900 cents"],
                         stop_reason="needs_staff_approval",
                         intended_actions=[action("process_refund", **DEMO_REFUND),
                                           action("send_message", ticket_id=DEMO_TICKET, body="Waiting on staff approval.",
                                                  citations=["POL-RET-02"])]))
    result = run_ticket(store, DEMO_TICKET, fake_retriever, llm)
    assert len(store.data["refunds"]) == refunds_before
    assert result.status == Status.AWAITING_APPROVAL
    assert not any(r.startswith("RF-") for r in result.receipt_ids)
    assert store.ticket_state(DEMO_TICKET)["status"] == "awaiting_approval"
    refused = [e for e in events(store, DEMO_TICKET) if e["event"] == "tool_result" and e["tool"] == "process_refund"]
    assert refused and refused[0]["ok"] is False and refused[0]["error"]["code"] == "NO_MATCHING_APPROVAL"


def test_resume_after_staff_approval_refunds_once_and_never_twice(store):
    pause = FakeLLM(output(status=Status.AWAITING_APPROVAL, unresolved_requirements=["staff approval for 114900 cents"],
                           stop_reason="needs_staff_approval"))
    run_ticket(store, DEMO_TICKET, fake_retriever, pause)
    approve(store, "APR-8013")

    def finish():
        return output(status=Status.RESOLVED, response="Your refund was issued.",
                      intended_actions=[action("process_refund", **DEMO_REFUND),
                                        action("send_message", ticket_id=DEMO_TICKET, body="Your refund was issued.",
                                               citations=["POL-RET-02"])])

    result = resume(store, DEMO_TICKET, fake_retriever, FakeLLM(finish()))
    assert result.status == Status.RESOLVED
    assert any(r.startswith("RF-") for r in result.receipt_ids)
    assert len(store.refunds(approval_id="APR-8013")) == 1

    again = resume(store, DEMO_TICKET, fake_retriever, FakeLLM(finish()))  # same actions replayed: saved receipts, no second refund
    assert len(store.refunds(approval_id="APR-8013")) == 1
    assert again.status == Status.FAILED_SAFE  # resolved cannot be saved again; the store keeps the real state
    assert store.ticket_state(DEMO_TICKET)["status"] == "resolved"


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
    run_ticket(store, INJECTION_TICKET, fake_retriever, llm)
    assert len(store.data["refunds"]) == refunds_before
    codes = [e["error"]["code"] for e in events(store, INJECTION_TICKET)
             if e["event"] == "tool_result" and e["tool"] == "process_refund"]
    assert codes == ["NO_MATCHING_APPROVAL"]


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
