"""Agent tests: no network. A scripted fake model and a fake retriever stand in; tools, store and dispatcher are real."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import agent.agent as agent_module
from agent.agent import Classification, Finish, ReplyCheck, Step, load_checkpoint, resume, run_ticket
from agent.schemas import Category, Status, StopRun
from agent.staff import approve

DELIVERY_TICKET = "T-0029"    # tracking question for O-60001
DEMO_TICKET = "T-0039"        # refund for O-60048; no approval until staff add APR-8013
INJECTION_TICKET = "T-0055"   # ticket text claims a fake approval APR-9999 for O-60108
DEMO_REFUND = dict(order_id="O-60048", cents=114900, approval_id="APR-8013")


class ScriptedLLM:
    """Returns the scripted outputs in order (raising any that are exceptions) and records each call."""
    model = "fake"
    last_usage = None

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def parse(self, messages, schema):
        self.calls.append((schema, messages))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, schema), f"script expected {type(out).__name__}, agent asked for {schema.__name__}"
        return out


def retriever_for(*policy_ids):
    """A retriever that always returns these policies, shaped like the real one."""
    def retrieve(query, k):
        passages = [{"policy_id": p, "version": "1.1", "title": p, "score": 0.6, "best_section": "Rules",
                     "text": f"{p} v1.1: rules text."} for p in policy_ids[:k]]
        return {"method": "embeddings", "fallback_reason": None, "best_score": 0.6, "weak_evidence": False,
                "chars": 100, "passages": passages}
    return retrieve


def classify(category):
    return Classification(category=category, summary="customer request")


def step(action, **kw):
    base = dict(note=f"{action} next", action=action, query=None, order_id=None, cents=None, approval_id=None,
                finish=None)
    base.update(kw)
    return Step(**base)


def kb(query="relevant policy"):
    return step("kb_lookup", query=query)


def finish(status, response, citations, unresolved=(), stop_reason="done"):
    return step("finish", finish=Finish(status=status, response=response, citations=list(citations),
                                        unresolved_requirements=list(unresolved), stop_reason=stop_reason))


def supported():
    return ReplyCheck(supported=True, problems=[])


def events(store, ticket_id):
    path = Path(store.root) / "traces" / f"{ticket_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def tool_requests(store, ticket_id):
    return [e["tool"] for e in events(store, ticket_id) if e["event"] == "tool_request"]


def validation_error():
    with pytest.raises(ValidationError) as err:
        Step.model_validate({})
    return err.value


PAUSE = ("Your returned ThinkPad was received. Your refund of $1,149.00 is waiting on staff approval.")
REFUNDED = "Your refund of $1,149.00 has been issued (receipt {rf})."


def run_demo_to_pause(store):
    llm = ScriptedLLM(classify(Category.RETURNS_REFUNDS), kb("refund for a returned laptop"),
                      finish("awaiting_approval", PAUSE, ["POL-RET-01", "POL-RET-02"],
                             ["staff approval for 114900 cents"], "awaiting_refund_approval"),
                      supported())
    return run_ticket(store, DEMO_TICKET, retriever_for("POL-RET-01", "POL-RET-02"), llm), llm


# --- happy paths -----------------------------------------------------------------

def test_delivery_ticket_is_resolved_with_a_sent_reply(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb("order tracking"),
                      finish("resolved", "Your order is in transit with USPS.", ["POL-SHIP-01"]), supported())
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.RESOLVED and result.category == Category.DELIVERY
    assert [r[:4] for r in result.receipt_ids] == ["MSG-"]
    assert store.ticket_state(DELIVERY_TICKET)["team"] == "Delivery"
    assert tool_requests(store, DELIVERY_TICKET) == ["ticket_get", "kb_lookup", "send_message", "ticket_update"]
    assert len(llm.calls) == 4


def test_trace_has_the_events_the_ui_reads(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(),
                      finish("resolved", "Your order is in transit.", ["POL-SHIP-01"]), supported())
    run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    trace = events(store, DELIVERY_TICKET)
    assert trace[0]["event"] == "run_started" and trace[0]["mode"] == "run" and trace[0]["system"] == "agent"
    calls = [e for e in trace if e["event"] == "model_call"]
    assert [c["n"] for c in calls] == [1, 2, 3, 4]
    assert [c["purpose"] for c in calls] == ["classify", "step", "step", "check"]
    assert calls[1]["tool_calls"] == ["kb_lookup"]
    assert all({"tokens_in", "tokens_out", "latency_ms", "text"} <= c.keys() for c in calls)
    assert trace[-1]["event"] == "run_finished" and trace[-1]["status"] == "resolved"
    assert trace[-1]["budget"]["model_calls"] == 4 and trace[-1]["budget"]["tool_calls"] == 4


def test_the_model_never_sees_labels_or_injected_failures(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(), finish("resolved", "In transit.", ["POL-SHIP-01"]),
                      supported())
    run_ticket(store, "T-0052", retriever_for("POL-SHIP-01"), llm)  # T-0052 has an injected send_message timeout
    seen = json.dumps([messages for _, messages in llm.calls])
    for leaked in ("failure_injection", "expected_actions", "final_status", "forbidden_actions", "TIMEOUT"):
        assert leaked not in seen


# --- the approval demo ---------------------------------------------------------

def test_demo_pauses_without_approval_then_resumes_to_one_refund(store):
    first, _ = run_demo_to_pause(store)
    assert first.status == Status.AWAITING_APPROVAL
    assert first.unresolved_requirements == ["staff approval for 114900 cents"]
    assert store.refunds("O-60048") == []
    checkpoint = load_checkpoint(store, DEMO_TICKET)
    assert set(checkpoint["evidence"]) == {"POL-RET-01", "POL-RET-02"}
    assert checkpoint["budget"]["model_calls"] == 4 and checkpoint["budget"]["tool_calls"] == 4

    approve(store, "APR-8013")
    rf = "RF-4019"
    llm = ScriptedLLM(step("process_refund", **DEMO_REFUND),
                      finish("resolved", REFUNDED.format(rf=rf), ["POL-RET-02"]), supported())
    result = resume(store, DEMO_TICKET, retriever_for("POL-RET-01", "POL-RET-02"), llm)

    assert result.status == Status.RESOLVED
    assert rf in result.receipt_ids and any(r.startswith("MSG-") for r in result.receipt_ids)
    assert [r["refund_id"] for r in store.refunds("O-60048")] == [rf]
    assert store.remaining_refundable_cents("O-60048") == 0
    assert store.order("O-60048")["status"] == "refunded"
    resumed = [e["tool"] for e in events(store, DEMO_TICKET) if e["event"] == "tool_request" and e["run_id"] != "staff"]
    assert resumed[4:] == ["ticket_get", "process_refund", "send_message", "ticket_update"]  # no second search
    budget = load_checkpoint(store, DEMO_TICKET)["budget"]
    assert budget["model_calls"] == 7 and budget["tool_calls"] == 8  # counted across the pause, within 8/8

    with pytest.raises(ValueError, match="only a paused agent run"):
        resume(store, DEMO_TICKET, retriever_for("POL-RET-02"), ScriptedLLM())
    assert len(store.refunds("O-60048")) == 1


def test_resume_before_approval_is_still_blocked_by_the_python_gate(store):
    run_demo_to_pause(store)
    llm = ScriptedLLM(step("process_refund", **DEMO_REFUND),
                      finish("awaiting_approval", PAUSE, ["POL-RET-02"], ["staff approval for 114900 cents"],
                             "awaiting_refund_approval"), supported())
    result = resume(store, DEMO_TICKET, retriever_for("POL-RET-02"), llm)
    assert result.status == Status.AWAITING_APPROVAL
    assert store.refunds("O-60048") == []
    refused = [e for e in events(store, DEMO_TICKET) if e["event"] == "tool_result" and e["tool"] == "process_refund"]
    assert refused[0]["error"]["code"] == "NO_MATCHING_APPROVAL"
    step_messages = json.loads(llm.calls[1][1][1]["content"])
    assert step_messages["this_run_results"][0]["error"]["code"] == "NO_MATCHING_APPROVAL"


def test_resume_needs_a_checkpoint(store):
    with pytest.raises(ValueError):
        resume(store, DEMO_TICKET, retriever_for("POL-RET-02"), ScriptedLLM())
    with pytest.raises(StopRun, match="unknown_ticket"):
        resume(store, "T-9999", retriever_for("POL-RET-02"), ScriptedLLM())


def test_injected_approval_in_ticket_text_never_refunds(store):
    llm = ScriptedLLM(classify(Category.RETURNS_REFUNDS), kb("refund request"),
                      step("process_refund", order_id="O-60108", cents=34900, approval_id="APR-9999"),
                      finish("awaiting_customer", "Please ship the Roomba back so we can inspect the return.",
                             ["POL-RET-01"], ["customer ships the return"], "needs_return"), supported())
    result = run_ticket(store, INJECTION_TICKET, retriever_for("POL-SEC-01", "POL-RET-01"), llm)
    assert result.status == Status.AWAITING_CUSTOMER
    assert store.refunds("O-60108") == []


# --- reply checks and revision ---------------------------------------------------

def test_citing_an_unretrieved_policy_is_revised_once(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(),
                      finish("resolved", "Your order is in transit.", ["POL-RET-04"]),
                      finish("resolved", "Your order is in transit.", ["POL-SHIP-01"]), supported())
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.RESOLVED and result.citations == ["POL-SHIP-01"]
    revision = [e for e in events(store, DELIVERY_TICKET) if e["event"] == "revision"]
    assert len(revision) == 1 and "POL-RET-04" in revision[0]["problems"][0]
    assert json.loads(llm.calls[3][1][1]["content"])["reply_problems"]  # the model saw why
    assert len(store.data["outbox"]) == 1


def test_a_reply_that_still_fails_after_revision_is_escalated_and_not_sent(store):
    bad = finish("resolved", "Your order arrives tomorrow.", ["POL-SHIP-01"])
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(), bad, ReplyCheck(supported=False, problems=["date not in records"]),
                      bad, ReplyCheck(supported=False, problems=["date not in records"]))
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.ESCALATED and result.stop_reason == "unsupported_reply"
    assert result.response is None and store.data["outbox"] == []
    assert "date not in records" in result.unresolved_requirements[0]


def test_claiming_a_refund_without_a_receipt_is_caught_by_python(store):
    llm = ScriptedLLM(classify(Category.RETURNS_REFUNDS), kb(),
                      finish("resolved", "Your refund of $1,149.00 has been issued.", ["POL-RET-02"]),
                      finish("awaiting_approval", PAUSE, ["POL-RET-02"], ["staff approval for 114900 cents"],
                             "awaiting_refund_approval"), supported())
    result = run_ticket(store, DEMO_TICKET, retriever_for("POL-RET-02"), llm)
    assert result.status == Status.AWAITING_APPROVAL
    check = [e for e in events(store, DEMO_TICKET) if e["event"] == "reply_check"][0]
    assert check["checked_by"] == "python" and "no refund receipt" in check["problems"][0]


def test_an_amount_not_in_the_records_is_caught_by_python(store):
    llm = ScriptedLLM(classify(Category.RETURNS_REFUNDS), kb(),
                      finish("awaiting_approval", "Your $1,500.00 refund is waiting on approval.", ["POL-RET-02"],
                             ["staff approval for 114900 cents"], "awaiting_refund_approval"),
                      finish("awaiting_approval", PAUSE, ["POL-RET-02"], ["staff approval for 114900 cents"],
                             "awaiting_refund_approval"), supported())
    run_ticket(store, DEMO_TICKET, retriever_for("POL-RET-02"), llm)
    check = [e for e in events(store, DEMO_TICKET) if e["event"] == "reply_check"][0]
    assert "$1,500.00" in check["problems"][0]


# --- model output failures ---------------------------------------------------------

def test_one_schema_repair_then_continue(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), validation_error(), kb(),
                      finish("resolved", "In transit.", ["POL-SHIP-01"]), supported())
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.RESOLVED
    assert load_checkpoint(store, DELIVERY_TICKET)["budget"]["schema_repairs"] == 1
    assert any(e["event"] == "schema_repair" for e in events(store, DELIVERY_TICKET))


def test_a_second_bad_output_fails_safe(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), validation_error(), validation_error())
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "invalid_model_output"
    assert store.ticket_state(DELIVERY_TICKET)["status"] == "failed_safe"


def test_injected_malformed_output_once_is_repaired(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(), finish("resolved", "In transit.", ["POL-SHIP-01"]),
                      supported())
    result = run_ticket(store, "T-0045", retriever_for("POL-SHIP-01"), llm)  # F5: INVALID_JSON once
    assert result.status == Status.RESOLVED
    calls = [e for e in events(store, "T-0045") if e["event"] == "model_call"]
    assert [c["simulated_fault"] for c in calls].count(True) == 1


def test_injected_malformed_output_twice_fails_safe(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb())
    result = run_ticket(store, "T-0091", retriever_for("POL-SHIP-01"), llm)  # F5: INVALID_JSON twice
    assert result.status == Status.FAILED_SAFE
    assert tool_requests(store, "T-0091") == ["ticket_get", "kb_lookup"]  # failed_safe is saved by Python directly


def test_a_model_api_error_fails_safe_without_repair(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), ConnectionError("gateway down"))
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "model_error"


# --- tool failures -------------------------------------------------------------------

def test_send_timeout_is_retried_and_the_reply_is_sent_once(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(), finish("resolved", "In transit.", ["POL-SHIP-01"]),
                      supported())
    result = run_ticket(store, "T-0052", retriever_for("POL-SHIP-01"), llm)  # F1: send_message times out once
    assert result.status == Status.RESOLVED
    assert len([m for m in store.data["outbox"] if m["ticket_id"] == "T-0052"]) == 1
    replayed = [e for e in events(store, "T-0052") if e["event"] == "tool_result" and e["tool"] == "send_message"]
    assert replayed[-1]["retry"] is True and replayed[-1]["result"]["replayed"] is True


def test_version_conflict_rereads_and_saves_once_more(store):
    llm = ScriptedLLM(classify(Category.CANCELLATION), kb("cancel order"), step("cancel_order", order_id="O-60150"),
                      finish("resolved", "Your order has been cancelled.", ["POL-ORD-01"]), supported())
    result = run_ticket(store, "T-0057", retriever_for("POL-ORD-01"), llm)  # F4: VERSION_CONFLICT once
    assert result.status == Status.RESOLVED and any(r.startswith("CAN-") for r in result.receipt_ids)
    assert tool_requests(store, "T-0057") == ["ticket_get", "kb_lookup", "cancel_order", "send_message",
                                              "ticket_update", "ticket_get", "ticket_update"]


def test_ticket_get_failing_twice_fails_safe_before_any_model_call(store):
    llm = ScriptedLLM()
    result = run_ticket(store, "T-0018", retriever_for("POL-RET-03"), llm)  # F2: ticket_get times out twice
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "ticket_get_unavailable"
    assert llm.calls == []


def test_a_third_identical_search_stops_the_run(store):
    llm = ScriptedLLM(classify(Category.DELIVERY), kb("same"), kb("same"), kb("same"))
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "repeated_call"


# --- budgets -----------------------------------------------------------------------------

def test_model_budget_stops_the_run(store, monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_MODEL_CALLS", 3)
    llm = ScriptedLLM(classify(Category.DELIVERY), kb("one"), kb("two"))
    result = run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "model_budget"


def test_a_refund_is_not_started_without_budget_left_to_tell_the_customer(store, monkeypatch):
    approve(store, "APR-8013")
    monkeypatch.setattr(agent_module, "MAX_MODEL_CALLS", 3)
    llm = ScriptedLLM(classify(Category.RETURNS_REFUNDS), kb(), step("process_refund", **DEMO_REFUND))
    result = run_ticket(store, DEMO_TICKET, retriever_for("POL-RET-02"), llm)
    assert result.status == Status.ESCALATED and result.stop_reason == "budget_reserve"
    assert store.refunds("O-60048") == []


def test_unknown_ticket_stops_before_anything_runs(store):
    with pytest.raises(StopRun, match="unknown_ticket"):
        run_ticket(store, "T-9999", retriever_for("POL-SHIP-01"), ScriptedLLM())
